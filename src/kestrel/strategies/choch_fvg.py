"""CHoCH + fair value gap at the New York open (research, YouTube batch 1 S2, Craig
Percoco), long only, as a resting limit order.

Each rule is fixed here so nothing is decided by eye:

1. Swing highs with ``swing_bars`` bars either side, known ``swing_bars`` bars later.
2. A bullish change of character: the last known swing high is below the one before it,
   and a bar (the break, bar t) closes above it. One per swing high. Only breaks whose
   bar opens between 13:00 and 16:00 UTC count (New York's 9:30 open in both daylight-
   saving states, and the hour or two after it).
3. The leg runs from the lowest low since that swing high to the break.
4. The leg's most recent bullish fair value gap (bar i's low above bar i-2's high) at
   least ``min_gap_atr_tenths`` / 10 x ATR(14) wide, known by the break's close. If none
   is, the next bar may complete one (the break's own push often leaves the gap): the
   order is then placed at that bar's close instead. Nothing later than the order's bar
   is used. (The cost gate measured the same setups but let the next bar's gap win over
   an older one; a strategy can't look ahead, so here the known gap comes first.)
5. A buy limit at the gap's midpoint, resting for ``expiry_bars`` bars; stop at bar
   i-2's low; target ``target_r`` times the risk from the limit.
6. At most ``MAX_PER_DAY`` orders a UTC day; the engine holds one trade at a time.
"""

from __future__ import annotations

from datetime import date, datetime, time

import polars as pl

from kestrel.indicators import atr

NY_OPEN = (time(13, 0), time(16, 0))
MAX_PER_DAY = 3
ATR_BARS = 14


def in_new_york_open(at: datetime) -> bool:
    return NY_OPEN[0] <= at.time() < NY_OPEN[1]


class ChochFvg:
    def __init__(
        self,
        symbol: str = "SOLUSDT",
        swing_bars: int = 3,
        min_gap_atr_tenths: int = 3,
        expiry_bars: int = 6,
        target_r: int = 4,
    ) -> None:
        self.symbol = symbol
        self.swing_bars, self.min_gap_atr_tenths = swing_bars, min_gap_atr_tenths
        self.expiry_bars, self.target_r = expiry_bars, target_r
        self.name = f"CHoCH + FVG NY open {target_r}R {symbol}"

    def orders(self, bars: pl.DataFrame) -> dict[int, tuple[float, float]]:
        """Row -> (limit, stop) for every order, placed at that row's close."""
        high, low, close = bars["high"].to_list(), bars["low"].to_list(), bars["close"].to_list()
        times = bars["open_time"].to_list()
        ranges = atr(bars, ATR_BARS).to_list()
        k, n = self.swing_bars, len(close)
        min_gap = self.min_gap_atr_tenths / 10

        def gap_at(i: int) -> tuple[float, float] | None:
            """The limit and stop of a qualifying gap completed by bar ``i``, if any."""
            if i < 2:
                return None
            gap, size = low[i] - high[i - 2], ranges[i]
            if gap > 0 and size is not None and gap >= min_gap * size:
                return (high[i - 2] + low[i]) / 2, low[i - 2]
            return None

        swing_highs: list[tuple[int, float]] = []
        used: int | None = None
        out: dict[int, tuple[float, float]] = {}
        per_day: dict[date, int] = {}
        waiting: int | None = None  # a break with no known gap yet: its leg's start
        for t in range(n):
            if waiting is not None:  # the bar after a break may complete the leg's gap
                found = gap_at(t) if t >= waiting + 2 else None
                if found is not None and per_day.get(times[t].date(), 0) < MAX_PER_DAY:
                    out[t] = found
                    per_day[times[t].date()] = per_day.get(times[t].date(), 0) + 1
                waiting = None
            j = t - k
            if j >= k and high[j] > max(high[j - k : j]) and high[j] >= max(high[j + 1 : t + 1]):
                swing_highs.append((j, high[j]))
            if len(swing_highs) < 2:
                continue
            (_, before), (last_at, last) = swing_highs[-2], swing_highs[-1]
            if not (last < before and close[t] > last and used != last_at):
                continue
            used = last_at
            if not in_new_york_open(times[t]):
                continue
            leg_low_at = min(range(last_at, t + 1), key=lambda i: low[i])
            known = None
            for i in range(t, max(leg_low_at + 2, 2) - 1, -1):  # the most recent gap first
                known = gap_at(i)
                if known is not None:
                    break
            if known is None:
                waiting = leg_low_at
                continue
            if per_day.get(times[t].date(), 0) < MAX_PER_DAY:
                out[t] = known
                per_day[times[t].date()] = per_day.get(times[t].date(), 0) + 1
        return out

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        n = bars.height
        orders = self.orders(bars)
        enter = [i in orders for i in range(n)]
        limit = [orders[i][0] if i in orders else None for i in range(n)]
        stop = [orders[i][1] if i in orders else None for i in range(n)]
        target = [
            orders[i][0] + self.target_r * (orders[i][0] - orders[i][1]) if i in orders else None
            for i in range(n)
        ]
        valid = [self.expiry_bars if i in orders else None for i in range(n)]
        return pl.DataFrame(
            {"enter": enter, "limit": limit, "stop": stop, "target": target, "valid_bars": valid},
            schema={
                "enter": pl.Boolean,
                "limit": pl.Float64,
                "stop": pl.Float64,
                "target": pl.Float64,
                "valid_bars": pl.Int64,
            },
        )
