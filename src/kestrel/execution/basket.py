"""One day of a basket (several coins) strategy: the portfolio engine's step, shared with
the paper runner so a basket trades the same way in a backtest and forward.

A day has two parts, in order:

1. ``open_day``: at the day's open, sell any held coin with no bar today at its last close
   (a delisting or a gap; a refused sale is recorded once and the coin is stranded), then
   trade towards the target decided at the previous close: sells first, then market buys,
   then resting limit buys, with the no-trade band.
2. ``close_day``: at the day's close, note each held coin's latest bar and mark the account.

Prices come from ``bars``: (symbol, open time) -> (open, close, quote volume, half-spread).
A bar's volume and spread are known at its close, so an order at today's open is costed
from yesterday's. The paper runner adds a live day's opens to ``bars`` as they form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side

Bar = tuple[Decimal, Decimal, Decimal, Decimal]
"""(open, close, quote volume, half-spread)."""
Target = dict[str, Decimal | None]
"""A decision: symbol -> weight, or None to keep a coin as held."""


class BasketVenue(Protocol):
    """What a basket needs from a venue: the simulated one, the risk guard around it, or an
    exchange account."""

    def holdings(self) -> dict[str, Decimal]: ...
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
    def cost_model(self) -> CostModel: ...


@dataclass
class Book:
    """What a basket run carries from one day to the next, besides the venue."""

    last_bar: dict[str, datetime] = field(default_factory=dict)
    """Each held coin's latest bar."""
    stranded: set[str] = field(default_factory=set)
    """Held coins whose forced sale was refused; never retried."""
    previous: Target = field(default_factory=dict)
    """The last decision traded, to tell a new target from an unchanged one."""


@dataclass
class DayResult:
    orders: list[tuple[datetime, Fill | Rejection]] = field(default_factory=list)
    """Every order, with when it executed."""
    forced_exits: int = 0
    band_skips: int = 0


def open_day(
    venue: BasketVenue,
    book: Book,
    day: datetime,
    before: datetime,
    bars: dict[tuple[str, datetime], Bar],
    target: Target | None,
    band: Decimal,
    duration: timedelta,
    limits: dict[str, Decimal] | None = None,
    lows: dict[tuple[str, datetime], Decimal] | None = None,
) -> DayResult:
    """Forced exits, then the trade of ``target`` (decided at ``before``'s close) at
    ``day``'s open."""
    out = DayResult()

    def submit(
        symbol: str, side: Side, qty: Decimal, price: Decimal, at: datetime, known: datetime
    ) -> Fill | Rejection:
        _, _, volume, spread = bars[(symbol, known)]
        venue.set_market(symbol, price, volume, spread)
        order = venue.submit_market(symbol, side, qty)
        out.orders.append((at, order))
        return order

    # Forced exits: a held coin with no bar today is sold at its last close.
    # A refused sale is recorded once: retrying at the same stale price can't succeed.
    for s, qty in venue.holdings().items():
        if (s, day) not in bars and s not in book.stranded:
            seen = book.last_bar[s]
            close = bars[(s, seen)][1]
            order = submit(s, Side.SELL, qty, close, seen + duration, seen)
            if isinstance(order, Fill):
                out.forced_exits += 1
            else:
                book.stranded.add(s)

    if target is None:
        return out
    opens = {s: bars[(s, day)][0] for s in set(target) | set(venue.holdings()) if (s, day) in bars}
    # A stranded coin has no price today: it counts at its last close and can't trade.
    holdings = venue.holdings()
    equity = venue.cash() + sum(
        q * (opens[s] if s in opens else bars[(s, book.last_bar[s])][1])
        for s, q in holdings.items()
    )
    current = {s: q * opens[s] / equity for s, q in holdings.items() if s in opens}
    wanted: dict[str, Decimal] = {}
    for s in set(target) | set(current):
        if s in opens:
            w = target.get(s, Decimal(0))
            wanted[s] = current.get(s, Decimal(0)) if w is None else w
    changes = {s: w - current.get(s, Decimal(0)) for s, w in wanted.items()}
    # Resizing within the band is skipped (and counted when the target changed);
    # a coin dropped from the target is always sold in full.
    for s, change in changes.items():
        new = wanted[s] != book.previous.get(s, Decimal(0))
        if wanted[s] > 0 and 0 < abs(change) <= band and new:
            out.band_skips += 1
    sells = [s for s, ch in changes.items() if ch < -band or (wanted[s] == 0 and s in current)]
    for s in sorted(sells):
        held = venue.position(s)
        qty = held if wanted[s] == 0 else min(held, -changes[s] * equity / opens[s])
        submit(s, Side.SELL, qty, opens[s], day, before)
    model = venue.cost_model()
    resting = limits or {}
    buys = sorted(s for s, ch in changes.items() if ch > band)
    # Market buys at the open first; limit buys rest during the bar.
    for s in [b for b in buys if b not in resting or b in current] + [
        b for b in buys if b in resting and b not in current
    ]:
        price = opens[s]
        if s in resting and s not in current:
            low = (lows or {})[(s, day)]
            if low > resting[s]:
                continue  # the bar never reached the limit: the order lapses
            price = min(resting[s], opens[s])
        _, _, volume, spread = bars[(s, before)]
        notional = min(changes[s] * equity, venue.cash())
        rate = model.cost_rate(Side.BUY, notional, volume, spread) if volume > 0 else Decimal(0)
        if notional > 0:
            submit(s, Side.BUY, notional / (1 + rate) / price, price, day, before)
    book.previous = target
    return out


def close_day(
    venue: BasketVenue, book: Book, day: datetime, bars: dict[tuple[str, datetime], Bar]
) -> Decimal:
    """Note each held coin's latest bar and return equity at ``day``'s close. A coin whose
    forced sale was refused (e.g. below the exchange minimum) is valued at its last close."""
    for s in venue.holdings():
        if (s, day) in bars:
            book.last_bar[s] = day
    value = sum(q * bars[(s, book.last_bar[s])][1] for s, q in venue.holdings().items())
    return venue.cash() + value
