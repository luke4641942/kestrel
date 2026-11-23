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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

import polars as pl

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side

__all__ = ["Fill", "Handled", "Ledger", "PaperAccount", "Rejection", "State", "Terms", "run"]
from kestrel.backtest.engine import half_spreads
from kestrel.backtest.portfolio import validated_decisions
from kestrel.backtest.spread import with_half_spread
from kestrel.config import Config
from kestrel.data.binance_vision import Month
from kestrel.data.panel import load_panel
from kestrel.data.schema import KLINE_SCHEMA, Interval
from kestrel.data.store import ParquetStore
from kestrel.execution.basket import Bar, Book, close_day, open_day
from kestrel.execution.rebalance import rebalance
from kestrel.execution.safety import RiskGuard, behind, check_mark, halt, halted, stale
from kestrel.execution.venue import SimVenue
from kestrel.strategy import Strategy, make_portfolio_strategy, make_strategy


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
    positions: dict[str, Decimal] = field(default_factory=dict)
    """A basket account's coins (a single-coin account's is in ``position``)."""
    book: dict[str, Any] = field(default_factory=dict)
    """A basket account's carry-over for kestrel.execution.basket (last bars, stranded
    coins, the previous target), as written to the ledger."""


def accounts(root: Path | str) -> list[str]:
    """Every paper account under ``<root>/paper``, by name."""
    base = Path(root) / "paper"
    return sorted(p.name for p in base.glob("*") if (p / "ledger.jsonl").exists())


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
        if "positions" in last:  # a basket account
            return State(
                Decimal(last["cash"]),
                Decimal(0),
                datetime.fromisoformat(last["bar"]),
                None,
                {k: Decimal(v) for k, v in last["positions"].items()},
                last["book"],
            )
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
    missed: bool = False
    """A live venue was behind: decided and recorded, not traded."""


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


def missing_inputs(bars: pl.DataFrame, pending: list[datetime]) -> str | None:
    """Which strategy inputs (columns beyond the kline schema, e.g. ``btc_close``) are
    unknown on a bar about to be decided. A strategy reads an unknown input as cash, so a
    live run would sell on missing data rather than on a signal: refuse instead."""
    extra = [c for c in bars.columns if c not in KLINE_SCHEMA]
    if not extra or not pending:
        return None
    due = bars.filter(pl.col("open_time").is_in(pending))
    found = []
    for column in extra:
        gaps = due.filter(pl.col(column).is_null())["open_time"].to_list()
        if gaps:
            found.append(f"{column} on {', '.join(f'{t:%Y-%m-%d}' for t in gaps[:3])}")
    return "; ".join(found) or None


def run(
    account: PaperAccount,
    bars: pl.DataFrame,
    config: Config,
    next_open: NextOpen = lambda _: None,
    strategy: Strategy | None = None,
    venue: PaperVenue | None = None,
    now: datetime | None = None,
) -> list[Handled]:
    """Handle every closed bar in ``bars`` (all of them closed, history included) that the
    account hasn't handled, oldest first, through ``venue`` (the simulated venue by
    default). A live venue fills now, so it can't trade a decision at a past open: when an
    account on a live venue is behind (the Mac was asleep), each missed bar is still marked
    and its decision recorded as missed, with no trade, and only the latest is traded.

    Safety (kestrel.execution.safety): a halted account is refused; with ``now`` (a live
    run, not a replay) stale bars are refused; every order passes the ``[risk]`` order
    limit and every mark the loss limits, and a breach halts the account before it trades."""
    reason = halted(account)
    if reason is not None:
        raise ValueError(
            f"{account.dir.name} is halted ({reason}); check it, then "
            "kestrel paper resume NAME --reason ..."
        )
    terms = account.terms()
    strategy = strategy or make_strategy(terms.strategy, terms.symbol, terms.params)  # type: ignore[assignment]
    assert strategy is not None
    state = account.state()
    times: list[datetime] = bars["open_time"].to_list()
    pending = [
        t for t in times
        if t >= terms.trade_from and (state.last_bar is None or t > state.last_bar)
    ]  # fmt: skip
    live = bool(getattr(venue, "live", False))
    latest = pending[-1] if pending else None
    if now is not None:
        problem = stale(bars, terms.interval, now)
        if problem:
            raise ValueError(f"not trading on stale data: {problem}")
        missing = missing_inputs(bars, pending)
        if missing:
            raise ValueError(f"not trading on missing inputs: {missing}")
    guard = RiskGuard(
        venue or SimVenue(config, terms.initial_cash, rules=config.rules_for), config.risk
    )
    venue = guard
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
        breach = check_mark(account, equity, config.risk)
        if breach:
            account.ledger.append(
                {"type": "mark", "bar": t, "close": Decimal(str(closes[i])), "equity": equity}
            )
            halt(account, breach)
            break
        weight = strategy.target_weights(bars.head(i + 1))[-1]
        target = Decimal(str(weight))
        mark = {"type": "mark", "bar": t, "close": Decimal(str(closes[i])), "equity": equity}
        if live and t != latest:
            # Missed: its next open has passed, and a live venue can only fill now.
            current = held * Decimal(str(closes[i])) / equity if equity else Decimal(0)
            account.ledger.append(mark)
            account.ledger.append(
                {"type": "decision", "bar": t, "target": target, "current": current,
                 "price": None, "band_skip": False, "missed": True}
            )  # fmt: skip
            account.ledger.append(
                {"type": "state", "bar": t, "cash": venue.cash(),
                 "position": venue.position(terms.symbol), "target": target}
            )  # fmt: skip
            handled.append(Handled(t, equity, target, None, missed=True))
            previous = target
            continue
        venue.set_market(terms.symbol, price, Decimal(str(volumes[i])), spreads[i])
        step = rebalance(
            venue, model, terms.symbol, target, price, Decimal(str(volumes[i])), band, spreads[i]
        )
        skipped = step.order is None and target != step.current and target != previous
        account.ledger.append(mark)
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
        if guard.tripped:
            halt(account, guard.tripped)
            break
    return handled


DELISTED_AFTER = timedelta(days=7)
"""A basket coin whose newest bar is older than this is treated as delisted, not stale."""

NextOpens = Callable[[datetime], dict[str, Decimal]]
"""Each coin's open of the bar starting at a time, for the coins trading; empty if the bar
hasn't started."""


@dataclass(frozen=True)
class BasketHandled:
    bar: datetime
    equity: Decimal
    target: dict[str, Decimal | None] | None
    """The decision at the bar's close; None on a day with no decision."""
    orders: list[Fill | Rejection]
    missed: bool = False


def _book_out(book: Book) -> dict[str, Any]:
    return {
        "last_bar": {s: t.isoformat() for s, t in book.last_bar.items()},
        "stranded": sorted(book.stranded),
        "previous": {s: None if w is None else str(w) for s, w in book.previous.items()},
    }


def _book_in(raw: dict[str, Any]) -> Book:
    return Book(
        {s: datetime.fromisoformat(t) for s, t in raw.get("last_bar", {}).items()},
        set(raw.get("stranded", [])),
        {s: None if w is None else Decimal(w) for s, w in raw.get("previous", {}).items()},
    )


BASKET_HISTORY = timedelta(days=500)
"""Stored history a basket run reads before its first decision (its rules need a year)."""


def basket_panel(
    store: ParquetStore, terms: Terms, coins: list[str], now: datetime
) -> pl.DataFrame:
    """The stored bars a basket account decides on: every coin it can hold, from a little
    over a year before its first decision to ``now``."""
    first = terms.trade_from - BASKET_HISTORY
    return load_panel(
        store, coins, terms.interval, Month(first.year, first.month), Month(now.year, now.month)
    )


def complete_opens(
    opens: dict[str, Decimal | None],
    last_seen: dict[str, datetime],
    at: datetime,
    duration: timedelta,
) -> dict[str, Decimal]:
    """A basket's forming opens at ``at``, or none (wait for the next run) while a coin
    that traded the bar before has no open yet: a price that hasn't arrived must never
    look like a delisting and force a sale. ``last_seen``: each coin's newest stored bar."""
    trading = {c for c, t in last_seen.items() if t >= at - duration}
    if any(opens.get(c) is None for c in trading):
        return {}
    return {c: o for c, o in opens.items() if o is not None}


def run_basket(
    account: PaperAccount,
    panel: pl.DataFrame,
    config: Config,
    next_opens: NextOpens = lambda _: {},
    venue: PaperVenue | None = None,
    now: datetime | None = None,
) -> list[BasketHandled]:
    """``run`` for a basket strategy (several coins): every closed bar in ``panel`` the
    account hasn't handled, through the portfolio engine's own day step
    (kestrel.execution.basket), so a day-by-day replay reproduces the portfolio backtest.

    Per bar: mark at its close, take the strategy's decision at that close (if it made
    one), then sell coins with no bar the next day and trade the decision at the next
    open. The next open is the stored bar's when replaying, or each coin's forming bar
    from ``next_opens`` when running today. Safety is as in ``run``."""
    reason = halted(account)
    if reason is not None:
        raise ValueError(
            f"{account.dir.name} is halted ({reason}); check it, then "
            "kestrel paper resume NAME --reason ..."
        )
    terms = account.terms()
    strategy = make_portfolio_strategy(terms.strategy, terms.params)
    decisions = validated_decisions(strategy, panel)
    targets: dict[datetime, dict[str, Decimal | None]] = {}
    limits: dict[datetime, dict[str, Decimal]] = {}
    for t, sym, w, limit in decisions.iter_rows():
        chosen = targets.setdefault(t, {})
        if sym is not None:
            chosen[sym] = None if w is None else Decimal(str(w))
            if limit is not None:
                limits.setdefault(t, {})[sym] = Decimal(str(limit))
    priced = with_half_spread(
        panel,
        config.universe.volume_window_days,
        [(float(v), float(bps) / 1e4) for v, bps in config.universe.spread_tiers],
        ["symbol", "history"],
    )
    bars: dict[tuple[str, datetime], Bar] = {
        (sym, t): (Decimal(str(o)), Decimal(str(c)), Decimal(str(v)), Decimal(str(h)))
        for sym, t, o, c, v, h in priced.select(
            "symbol", "open_time", "open", "close", "quote_volume", "half_spread"
        ).iter_rows()
    }
    lows = (
        {
            (sym, t): Decimal(str(lo))
            for sym, t, lo in panel.select("symbol", "open_time", "low").iter_rows()
        }
        if limits
        else {}
    )
    dates: list[datetime] = panel["open_time"].unique().sort().to_list()
    stored_days = set(dates)
    state = account.state()
    book = _book_in(state.book)
    pending = [
        t for t in dates
        if t >= terms.trade_from and (state.last_bar is None or t > state.last_bar)
    ]  # fmt: skip
    live = bool(getattr(venue, "live", False))
    latest = pending[-1] if pending else None
    if now is not None:
        # Each coin must be fresh (its quality was audited when it was brought up to
        # date). A coin whose bars stopped over a week ago has been delisted: the day step
        # sells it at its last close, as the backtest does, so it doesn't stop the run.
        newest = panel.group_by("symbol").agg(pl.col("open_time").max()).sort("symbol")
        for sym, last in newest.iter_rows():
            problem = behind(last, terms.interval, now)
            if problem and now - last <= DELISTED_AFTER:
                raise ValueError(f"not trading on stale data: {sym}: {problem}")
    guard = RiskGuard(
        venue or SimVenue(config, terms.initial_cash, rules=config.rules_for), config.risk
    )
    guard.restore(state.cash, state.positions)
    band = config.backtest.no_trade_band
    duration = terms.interval.duration
    handled: list[BasketHandled] = []
    for t in pending:
        after = t + duration
        forming: dict[str, Decimal] = {}
        if after not in stored_days:
            forming = next_opens(after)
            if not forming:
                break  # the next bar hasn't opened: pick up from here next run
            for sym, price in forming.items():
                bars[(sym, after)] = (price, price, Decimal(0), Decimal(0))
        equity = close_day(guard, book, t, bars)
        breach = check_mark(account, equity, config.risk)
        mark = {"type": "mark", "bar": t, "equity": equity}
        if breach:
            account.ledger.append(mark)
            halt(account, breach)
            break
        target = targets.get(t)
        decision = {
            "type": "decision",
            "bar": t,
            "targets": None
            if target is None
            else {k: None if w is None else str(w) for k, w in target.items()},
        }
        if forming:
            # The forming bar's opens it traded at, so tracking can replay this day before
            # that bar is stored (kestrel.execution.tracking.track_basket).
            decision["opens"] = {k: str(v) for k, v in forming.items()}
        if live and t != latest:
            account.ledger.append(mark)
            account.ledger.append({**decision, "missed": True})
            account.ledger.append(_state_event(t, guard, book))
            handled.append(BasketHandled(t, equity, target, [], missed=True))
            continue
        step = open_day(guard, book, after, t, bars, target, band, duration, limits.get(t), lows)
        account.ledger.append(mark)
        account.ledger.append(decision)
        for at, order in step.orders:
            account.ledger.append(_order_event(at, order))
        account.ledger.append(_state_event(t, guard, book))
        handled.append(BasketHandled(t, equity, target, [o for _, o in step.orders]))
        if guard.tripped:
            halt(account, guard.tripped)
            break
    return handled


def _order_event(at: datetime, order: Fill | Rejection) -> dict[str, Any]:
    if isinstance(order, Fill):
        return {
            "type": "fill",
            "at": at,
            "symbol": order.symbol,
            "side": order.side.value,
            "quantity": order.quantity,
            "reference_price": order.reference_price,
            "price": order.price,
            "fee": order.fee,
            "spread_cost": order.spread_cost,
            "impact_cost": order.impact_cost,
        }
    return {
        "type": "rejection",
        "at": at,
        "symbol": order.symbol,
        "side": order.side.value,
        "quantity": order.requested_quantity,
        "reason": order.reason,
    }


def _state_event(t: datetime, venue: RiskGuard, book: Book) -> dict[str, Any]:
    return {
        "type": "state",
        "bar": t,
        "cash": venue.cash(),
        "positions": dict(sorted(venue.holdings().items())),
        "book": _book_out(book),
    }
