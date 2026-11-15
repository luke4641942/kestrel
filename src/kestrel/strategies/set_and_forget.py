"""Set and Forget (research, YouTube batch 1 S1, "Alex"), long only, on 1H bars.

The original is a forex method with zones drawn by eye. Here every rule is mechanical,
so this tests a coded cousin of it, not the trader's judgement:

1. Higher timeframes are built from the 1H bars: 4H bars from 00:00 UTC, daily bars,
   and weekly bars from Monday 00:00 UTC. A higher-timeframe bar is used only once it
   has closed (its last hour is over), so a partial bar never counts.
2. Swings: a swing high is a bar whose high is above the ``swing_bars`` bars before it
   and at least the ``swing_bars`` after it (lows mirrored); it is known once the last
   of those later bars has closed.
3. Bias: on the weekly and on the daily, the last two known swing highs are rising and
   the last two known swing lows are rising (higher highs and higher lows).
4. Zones: 4H swing lows known by now from the last ``lookback_days`` days. A zone is a
   band no wider than ``zone_atr_tenths`` / 10 x the daily ATR(14) holding at least
   ``touches`` of them, from the lowest to the highest of those lows.
5. Entry: while the bias is up, a 1H bullish engulfing bar (the previous bar fell; this
   one opens at or below its close and closes above its open) whose low is inside a zone
   and whose close is above the zone's bottom. When the low is inside several zones, the
   lowest zone's bottom is used (the widest stop).
6. Buy at the next open; stop at the zone's bottom less 0.1 x the daily ATR(14); target
   ``target_r`` times the risk from the signal close. The engine holds one trade at a
   time.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import polars as pl

from kestrel.indicators import atr

STOP_ATR = 0.1
ATR_DAYS = 14
PERIODS = {"4h": timedelta(hours=4), "1d": timedelta(days=1), "1w": timedelta(weeks=1)}


def resample(bars: pl.DataFrame, every: str) -> pl.DataFrame:
    """Higher-timeframe bars from shorter ones, each with the time it closes (``end``)."""
    start_by: Literal["monday", "window"] = "monday" if every == "1w" else "window"
    out = (
        bars.sort("open_time")
        .group_by_dynamic("open_time", every=every, start_by=start_by)
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
        )
    )
    return out.with_columns(end=pl.col("open_time") + PERIODS[every])


@dataclass(frozen=True)
class Swing:
    known_at: datetime
    """When the bar that confirms it closes."""
    at: datetime
    """The swing bar's open time."""
    price: float


def swings(frame: pl.DataFrame, k: int) -> tuple[list[Swing], list[Swing]]:
    """(swing highs, swing lows) of a resampled frame, oldest first."""
    high, low = frame["high"].to_list(), frame["low"].to_list()
    starts, ends = frame["open_time"].to_list(), frame["end"].to_list()
    highs: list[Swing] = []
    lows: list[Swing] = []
    for j in range(k, len(high) - k):
        if high[j] > max(high[j - k : j]) and high[j] >= max(high[j + 1 : j + k + 1]):
            highs.append(Swing(ends[j + k], starts[j], high[j]))
        if low[j] < min(low[j - k : j]) and low[j] <= min(low[j + 1 : j + k + 1]):
            lows.append(Swing(ends[j + k], starts[j], low[j]))
    return highs, lows


def rising(found: list[Swing], known: list[datetime], now: datetime) -> bool:
    """Are the last two swings known by ``now`` rising? ``known`` is their known_at list."""
    n = bisect_right(known, now)
    return n >= 2 and found[n - 1].price > found[n - 2].price


def zones(lows: list[float], width: float, touches: int) -> list[tuple[float, float]]:
    """Every band no wider than ``width`` holding at least ``touches`` lows, as (bottom,
    top): each run of sorted lows starting at one low and reaching as far as the width
    allows."""
    ordered = sorted(lows)
    out: list[tuple[float, float]] = []
    j = 0
    for i, bottom in enumerate(ordered):
        j = max(j, i)
        while j + 1 < len(ordered) and ordered[j + 1] - bottom <= width:
            j += 1
        if j - i + 1 >= touches:
            out.append((bottom, ordered[j]))
    return out


def engulfing(o: list[float], c: list[float], t: int) -> bool:
    return t >= 1 and c[t - 1] < o[t - 1] and o[t] <= c[t - 1] and c[t] > o[t - 1]


class SetAndForget:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        swing_bars: int = 3,
        touches: int = 3,
        zone_atr_tenths: int = 5,
        lookback_days: int = 90,
        target_r: int = 3,
    ) -> None:
        self.symbol = symbol
        self.swing_bars, self.touches = swing_bars, touches
        self.zone_atr_tenths, self.lookback_days = zone_atr_tenths, lookback_days
        self.target_r = target_r
        self.name = f"set and forget {target_r}R {symbol}"

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        n = bars.height
        enter = [False] * n
        stop: list[float | None] = [None] * n
        target: list[float | None] = [None] * n
        schema = {"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64}
        if n < 2:
            return pl.DataFrame({"enter": enter, "stop": stop, "target": target}, schema=schema)
        times = bars["open_time"].to_list()
        step = times[1] - times[0]
        o, lo, c = bars["open"].to_list(), bars["low"].to_list(), bars["close"].to_list()
        k = self.swing_bars

        daily = resample(bars, "1d")
        weekly = resample(bars, "1w")
        four = resample(bars, "4h")
        bias = []
        for frame in (weekly, daily):
            highs, lows = swings(frame, k)
            bias.append((highs, [s.known_at for s in highs], lows, [s.known_at for s in lows]))
        _, four_lows = swings(four, k)
        four_known = [s.known_at for s in four_lows]
        daily_atr = atr(daily, ATR_DAYS).to_list()
        daily_end = daily["end"].to_list()
        lookback = timedelta(days=self.lookback_days)

        for t in range(1, n):
            if not engulfing(o, c, t):
                continue
            now = times[t] + step  # this bar's close
            if not all(rising(h, hk, now) and rising(lw, lk, now) for h, hk, lw, lk in bias):
                continue
            d = bisect_right(daily_end, now)  # daily bars closed by now
            size = daily_atr[d - 1] if d else None
            if size is None:
                continue
            known = four_lows[: bisect_right(four_known, now)]
            recent = [s.price for s in known if s.at >= now - lookback]
            width = self.zone_atr_tenths / 10 * size
            inside = [z for z in zones(recent, width, self.touches) if z[0] <= lo[t] <= z[1]]
            if not inside:
                continue
            bottom = min(z[0] for z in inside)
            level = bottom - STOP_ATR * size
            if not (c[t] > bottom and level < c[t]):
                continue
            enter[t], stop[t] = True, level
            target[t] = c[t] + self.target_r * (c[t] - level)
        return pl.DataFrame({"enter": enter, "stop": stop, "target": target}, schema=schema)
