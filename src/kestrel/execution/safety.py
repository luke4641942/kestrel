"""Safety for paper and live trading: halts, risk limits, stale data, reconciliation, kill.

Nothing here un-halts itself. A breached limit, a failed reconciliation or ``kestrel kill``
writes a ``halt`` event to the account's ledger, and the runner refuses to trade until a
person runs ``kestrel paper resume`` with a reason, which is recorded too.

- **Risk limits** (``[risk]`` in kestrel.toml): every daily mark is checked for a loss
  bigger than ``max_daily_loss`` since the previous mark and a drawdown bigger than
  ``max_drawdown`` from the account's peak; every order is checked against
  ``max_order_usdt`` before it is sent. A breach halts before anything trades.
- **Stale data**: the latest stored bar must be the one that closed most recently, and the
  last ``RECENT_BARS`` must have no gaps. Stale data stops the run without halting: it is
  usually an outage, and the next run catches up.
- **Reconciliation**: for an exchange account, the exchange must hold at least the coin and
  cash the ledger says the allocation holds. Less means the ledger and the exchange
  disagree, and the account halts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import polars as pl

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side
from kestrel.config import RiskConfig
from kestrel.data.quality import check_klines
from kestrel.data.schema import Interval

if TYPE_CHECKING:  # pragma: no cover
    from kestrel.execution.paper import PaperAccount, PaperVenue

RECENT_BARS = 30


# ---------- halts ----------


def halted(account: PaperAccount) -> str | None:
    """Why the account is halted, or None if it may trade."""
    for event in reversed(account.ledger.events()):
        if event["type"] == "resume":
            return None
        if event["type"] == "halt":
            return str(event["reason"])
    return None


def halt(account: PaperAccount, reason: str) -> None:
    account.ledger.append({"type": "halt", "at": datetime.now(UTC), "reason": reason})


def resume(account: PaperAccount, reason: str) -> None:
    if halted(account) is None:
        raise ValueError(f"{account.dir.name} isn't halted")
    if not reason.strip():
        raise ValueError("record why it is safe to resume")
    account.ledger.append({"type": "resume", "at": datetime.now(UTC), "reason": reason.strip()})


# ---------- limits ----------


def check_mark(account: PaperAccount, equity: Decimal, risk: RiskConfig) -> str | None:
    """A breach of the loss limits at a new mark, given the marks before it."""
    marks = [Decimal(e["equity"]) for e in account.ledger.events() if e["type"] == "mark"]
    if not marks:
        return None
    previous, peak = marks[-1], max([*marks, equity])
    if previous > 0 and equity < previous * (1 - risk.max_daily_loss):
        return (
            f"equity fell {1 - equity / previous:.1%} since the last mark "
            f"(limit {risk.max_daily_loss:.0%}): {previous:.2f} to {equity:.2f}"
        )
    if peak > 0 and equity < peak * (1 - risk.max_drawdown):
        return (
            f"drawdown {1 - equity / peak:.1%} from the peak of {peak:.2f} "
            f"(limit {risk.max_drawdown:.0%})"
        )
    return None


class RiskGuard:
    """A venue that refuses any order over ``max_order_usdt`` before it reaches the real one,
    and remembers that it did (the runner then halts)."""

    def __init__(self, venue: PaperVenue, risk: RiskConfig) -> None:
        self._venue = venue
        self._risk = risk
        self._reference: Decimal | None = None
        self.tripped: str | None = None
        self.live = getattr(venue, "live", False)

    def set_market(
        self,
        symbol: str,
        reference_price: Decimal,
        last_bar_volume: Decimal,
        half_spread: Decimal | None = None,
    ) -> None:
        self._reference = reference_price
        self._venue.set_market(symbol, reference_price, last_bar_volume, half_spread)

    def submit_market(self, symbol: str, side: Side, quantity: Decimal) -> Fill | Rejection:
        notional = quantity * (self._reference or Decimal(0))
        if notional > self._risk.max_order_usdt:
            self.tripped = (
                f"an order of {notional:.2f} USDT is over the {self._risk.max_order_usdt} limit"
            )
            return Rejection(symbol, side, quantity, f"risk limit: {self.tripped}")
        return self._venue.submit_market(symbol, side, quantity)

    def holdings(self) -> dict[str, Decimal]:
        """Every coin held (a basket account's venues offer it)."""
        return self._venue.holdings()  # type: ignore[attr-defined, no-any-return]

    def cash(self) -> Decimal:
        return self._venue.cash()

    def position(self, symbol: str) -> Decimal:
        return self._venue.position(symbol)

    def restore(self, cash: Decimal, positions: dict[str, Decimal]) -> None:
        self._venue.restore(cash, positions)

    def cost_model(self) -> CostModel:
        return self._venue.cost_model()


# ---------- data ----------


def behind(last: datetime, interval: Interval, now: datetime) -> str | None:
    """Why a market whose newest bar opened at ``last`` is behind ``now``, or None."""
    step = interval.duration.total_seconds()
    expected = datetime.fromtimestamp((now.timestamp() // step - 1) * step, UTC)
    if last < expected:
        return f"the latest bar is {last:%Y-%m-%d %H:%M} UTC; {expected:%Y-%m-%d %H:%M} has closed"
    return None


def stale(bars: pl.DataFrame, interval: Interval, now: datetime) -> str | None:
    """Why the bars aren't fit to decide on now, or None."""
    if bars.is_empty():
        return "no bars stored"
    last: datetime = bars["open_time"].max()  # type: ignore[assignment]
    late = behind(last, interval, now)
    if late:
        return late
    recent = check_klines(bars.tail(RECENT_BARS), interval)
    if recent.missing_bars:
        return f"{recent.missing_bars} bars missing in the last {RECENT_BARS}"
    return None


# ---------- reconciliation ----------


@dataclass(frozen=True)
class Holding:
    asset: str
    ledger: Decimal
    exchange: Decimal


def reconcile(holdings: list[tuple[str, Decimal]], balances: dict[str, Decimal]) -> list[Holding]:
    """Assets where the exchange holds less than the ledger says: empty when they agree."""
    return [
        Holding(asset, amount, balances.get(asset, Decimal(0)))
        for asset, amount in holdings
        if balances.get(asset, Decimal(0)) < amount
    ]


def describe(short: list[Holding]) -> str:
    return "; ".join(f"{h.asset}: ledger {h.ledger}, exchange holds {h.exchange}" for h in short)


# ---------- kill ----------


def kill(
    account: PaperAccount, venue: PaperVenue, price: Decimal, volume: Decimal, reason: str
) -> dict[str, Any]:
    """Sell everything at market now, then halt. ``price`` and ``volume`` (the last closed
    bar's quote volume) size the simulated venue's costs; an exchange fills at the market.
    Returns the sale's ledger event."""
    terms, state = account.terms(), account.state()
    venue.restore(state.cash, {terms.symbol: state.position} if state.position else {})
    event: dict[str, Any] = {"type": "kill", "at": datetime.now(UTC), "price": price}
    if state.position > 0:
        venue.set_market(terms.symbol, price, volume)
        order = venue.submit_market(terms.symbol, Side.SELL, state.position)
        if isinstance(order, Fill):
            event |= {"side": "sell", "quantity": order.quantity, "fill_price": order.price}
        else:
            event |= {"rejected": order.reason}
    account.ledger.append(event)
    account.ledger.append(
        {
            "type": "state",
            "bar": state.last_bar or terms.trade_from,
            "cash": venue.cash(),
            "position": venue.position(terms.symbol),
            "target": Decimal(0),
        }
    )
    halt(account, f"killed: {reason}")
    return event
