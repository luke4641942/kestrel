"""Paper trading: a strategy run forward, one closed bar at a time, through a venue.

The same decisions as the backtest, by construction. For each closed bar not yet handled:

1. mark the account at the bar's close (equity after the trade at its open);
2. work out the strategy's target from every bar up to and including it;
3. trade towards that target at the next bar's open, through the shared
   ``kestrel.execution.rebalance`` step and the no-trade band.

The next bar's open is the stored bar's when replaying history, and the open of the bar
still forming (from Binance's live API) when running today. If it isn't known yet, the run
stops and picks up from there next time.

Everything goes into an append-only ledger, ``<data>/paper/<account>/ledger.jsonl``: the
account's terms when it opened, every mark, decision, fill and rejection, and the cash and
position after each step. Nothing is edited; the account resumes from its last state. P4 S4
reconciles that state with the exchange's balances.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

import polars as pl

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side

__all__ = ["Fill", "Handled", "Ledger", "PaperAccount", "Rejection", "State", "Terms", "run"]
from kestrel.backtest.engine import half_spreads
from kestrel.config import Config
from kestrel.data.schema import Interval
from kestrel.execution.rebalance import rebalance
from kestrel.execution.venue import SimVenue
from kestrel.strategy import Strategy, make_strategy


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        # Zero keeps an exponent after arithmetic (0E+5, 0E-8): write it one way, so the
        # same account always writes the same ledger.
        return "0" if value == 0 else str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"can't write {type(value).__name__} to the ledger")


class Ledger:
    """One JSON object per line, appended, never edited."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(event, default=_json) + "\n")

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]


@dataclass(frozen=True)
class Terms:
    """What an account was opened to do; fixed for its life."""

    name: str
    strategy: str
    symbol: str
    interval: Interval
    params: dict[str, int]
    initial_cash: Decimal
    trade_from: datetime
    """The first bar it handles: its close is the first decision."""
    research: str | None = None
    """The research note it runs under, for a research strategy."""
    venue: Literal["sim", "testnet"] = "sim"
    """Where its orders go: the simulated venue, or Binance's Spot Testnet."""


@dataclass(frozen=True)
class State:
    cash: Decimal
    position: Decimal
    last_bar: datetime | None
    """The last closed bar handled (marked, decided, traded at the next open)."""
    last_target: Decimal | None


class PaperAccount:
    def __init__(self, root: Path | str, name: str) -> None:
        self.dir = Path(root) / "paper" / name
        self.ledger = Ledger(self.dir / "ledger.jsonl")

    @classmethod
    def open(cls, root: Path | str, terms: Terms) -> PaperAccount:
        account = cls(root, terms.name)
        if account.ledger.path.exists():
            raise ValueError(f"paper account {terms.name} already exists")
        account.ledger.append(
            {
                "type": "open",
                "at": datetime.now(UTC),
                "name": terms.name,
                "strategy": terms.strategy,
                "symbol": terms.symbol,
                "interval": terms.interval.value,
                "params": terms.params,
                "initial_cash": terms.initial_cash,
                "trade_from": terms.trade_from,
                "research": terms.research,
                "venue": terms.venue,
            }
        )
        return account

    def terms(self) -> Terms:
        events = self.ledger.events()
        if not events or events[0]["type"] != "open":
            raise ValueError(f"no paper account at {self.dir}")
        e = events[0]
        return Terms(
            e["name"],
            e["strategy"],
            e["symbol"],
            Interval(e["interval"]),
            e["params"],
            Decimal(e["initial_cash"]),
            datetime.fromisoformat(e["trade_from"]),
            e.get("research"),
            e.get("venue", "sim"),
        )

    def state(self) -> State:
        terms = self.terms()
        last = next((e for e in reversed(self.ledger.events()) if e["type"] == "state"), None)
        if last is None:
            return State(terms.initial_cash, Decimal(0), None, None)
        return State(
            Decimal(last["cash"]),
            Decimal(last["position"]),
            datetime.fromisoformat(last["bar"]),
            Decimal(last["target"]),
        )


@dataclass(frozen=True)
class Handled:
    bar: datetime
    equity: Decimal
    target: Decimal
    order: Fill | Rejection | None


class PaperVenue(Protocol):
    """What the runner needs from a venue: SimVenue offers it, and so does BinanceVenue."""

    def cash(self) -> Decimal: ...
    def position(self, symbol: str) -> Decimal: ...
    def submit_market(self, symbol: str, side: Side, quantity: Decimal) -> Fill | Rejection: ...
    def set_market(
        self,
        symbol: str,
        reference_price: Decimal,
        last_bar_volume: Decimal,
        half_spread: Decimal | None = None,
    ) -> None: ...
    def restore(self, cash: Decimal, positions: dict[str, Decimal]) -> None: ...
    def cost_model(self) -> CostModel: ...


NextOpen = Callable[[datetime], Decimal | None]
"""The open of the bar starting at a time, if it has started; None if not yet."""


def run(
    account: PaperAccount,
    bars: pl.DataFrame,
    config: Config,
    next_open: NextOpen = lambda _: None,
    strategy: Strategy | None = None,
    venue: PaperVenue | None = None,
) -> list[Handled]:
    """Handle every closed bar in ``bars`` (all of them closed, history included) that the
    account hasn't handled, oldest first, through ``venue`` (the simulated venue by
    default). A live venue fills now, so it can't trade a decision at a past open: an
    account more than one bar behind on a live venue is refused, not traded late."""
    terms = account.terms()
    strategy = strategy or make_strategy(terms.strategy, terms.symbol, terms.params)  # type: ignore[assignment]
    assert strategy is not None
    state = account.state()
    times: list[datetime] = bars["open_time"].to_list()
    pending = [
        t for t in times
        if t >= terms.trade_from and (state.last_bar is None or t > state.last_bar)
    ]  # fmt: skip
    if getattr(venue, "live", False) and len(pending) > 1:
        raise ValueError(
            f"{terms.name} is {len(pending)} bars behind; a live venue can only trade the "
            "latest decision (catching up is P4 S5)"
        )
    venue = venue or SimVenue(config, terms.initial_cash, rules=config.rules_for)
    venue.restore(state.cash, {terms.symbol: state.position} if state.position else {})
    model = venue.cost_model()
    band = config.backtest.no_trade_band
    opens, closes = bars["open"].to_list(), bars["close"].to_list()
    volumes = bars["quote_volume"].to_list()
    spreads = half_spreads(bars, config, terms.symbol)
    previous = state.last_target
    handled: list[Handled] = []
    for i, t in enumerate(times):
        if t < terms.trade_from or (state.last_bar is not None and t <= state.last_bar):
            continue
        after = t + terms.interval.duration
        price = (
            Decimal(str(opens[i + 1]))
            if i + 1 < len(times) and times[i + 1] == after
            else next_open(after)
        )
        if price is None:
            break  # the next bar hasn't opened: pick up from here next run
        held = venue.position(terms.symbol)
        equity = venue.cash() + held * Decimal(str(closes[i]))
        weight = strategy.target_weights(bars.head(i + 1))[-1]
        target = Decimal(str(weight))
        venue.set_market(terms.symbol, price, Decimal(str(volumes[i])), spreads[i])
        step = rebalance(
            venue, model, terms.symbol, target, price, Decimal(str(volumes[i])), band, spreads[i]
        )
        skipped = step.order is None and target != step.current and target != previous
        account.ledger.append(
            {"type": "mark", "bar": t, "close": Decimal(str(closes[i])), "equity": equity}
        )
        account.ledger.append(
            {
                "type": "decision",
                "bar": t,
                "target": target,
                "current": step.current,
                "price": price,
                "band_skip": skipped,
            }
        )
        if isinstance(step.order, Fill):
            f = step.order
            account.ledger.append(
                {
                    "type": "fill",
                    "at": after,
                    "side": f.side.value,
                    "quantity": f.quantity,
                    "reference_price": f.reference_price,
                    "price": f.price,
                    "fee": f.fee,
                    "spread_cost": f.spread_cost,
                    "impact_cost": f.impact_cost,
                }
            )
        elif isinstance(step.order, Rejection):
            r = step.order
            account.ledger.append(
                {
                    "type": "rejection",
                    "at": after,
                    "side": r.side.value,
                    "quantity": r.requested_quantity,
                    "reason": r.reason,
                }
            )
        account.ledger.append(
            {
                "type": "state",
                "bar": t,
                "cash": venue.cash(),
                "position": venue.position(terms.symbol),
                "target": target,
            }
        )
        handled.append(Handled(t, equity, target, step.order))
        previous = target
    return handled
