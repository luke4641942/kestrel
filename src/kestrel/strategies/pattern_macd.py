"""Candle pattern + breakout + MACD turn (research, YouTube batch 3 G5, guest trader).

Long only. Each rule is fixed here so nothing is decided by eye:

1. A bullish candle pattern on bar p, at a swing low (its low is the lowest of the last
   ``swing_bars`` bars):
   - pin bar: the lower wick is at least two thirds of the bar's range;
   - inside bar: its high below and its low above the previous bar's;
   - outside bar: its high above and its low below the previous bar's;
   - bullish engulfing: a rising bar whose body covers the previous falling bar's body.
2. Breakout: within ``expiry_bars`` bars after p, a bar closes above p's high.
3. MACD (12, 26, 9) turns up on that bar: its histogram is above the previous bar's.
4. Entry at the next open. Stop at p's low (the original states no stop; the spec's
   default is beyond the pattern's extreme). Target ``target_r`` times the risk from the
   signal close (the spec's 1.5-2R; his supply/demand zones are his own indicator).

A pattern is used once: its first qualifying breakout signals, or it expires. A newer
pattern at a swing low replaces one still waiting.
"""

from __future__ import annotations

import polars as pl

from kestrel.indicators import macd

PIN_WICK = 2 / 3


def bullish_pattern(
    o: list[float], h: list[float], lo: list[float], c: list[float], i: int
) -> bool:
    """Is bar ``i`` a pin bar, inside bar, outside bar or bullish engulfing?"""
    span = h[i] - lo[i]
    pin = span > 0 and (min(o[i], c[i]) - lo[i]) >= PIN_WICK * span
    if i == 0:
        return pin
    inside = h[i] < h[i - 1] and lo[i] > lo[i - 1]
    outside = h[i] > h[i - 1] and lo[i] < lo[i - 1]
    engulfing = c[i] > o[i] and c[i - 1] < o[i - 1] and o[i] <= c[i - 1] and c[i] >= o[i - 1]
    return pin or inside or outside or engulfing


class PatternMacd:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        swing_bars: int = 10,
        expiry_bars: int = 3,
        target_r: int = 2,
    ) -> None:
        self.symbol = symbol
        self.swing_bars, self.expiry_bars, self.target_r = swing_bars, expiry_bars, target_r
        self.name = f"pattern + MACD turn {target_r}R {symbol}"

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        o, h = bars["open"].to_list(), bars["high"].to_list()
        lo, c = bars["low"].to_list(), bars["close"].to_list()
        hist = macd(bars["close"]).histogram.to_list()
        n = len(c)
        enter = [False] * n
        stop: list[float | None] = [None] * n
        target: list[float | None] = [None] * n
        pattern: int | None = None
        for t in range(n):
            if pattern is not None:
                if t - pattern > self.expiry_bars:
                    pattern = None
                elif (
                    c[t] > h[pattern]
                    and hist[t] is not None
                    and hist[t - 1] is not None
                    and hist[t] > hist[t - 1]
                    and c[t] > lo[pattern]
                ):
                    enter[t] = True
                    stop[t] = lo[pattern]
                    target[t] = c[t] + self.target_r * (c[t] - lo[pattern])
                    pattern = None
                    continue
            at_swing_low = t + 1 >= self.swing_bars and lo[t] <= min(
                lo[t - self.swing_bars + 1 : t + 1]
            )
            if at_swing_low and bullish_pattern(o, h, lo, c, t):
                pattern = t
        return pl.DataFrame(
            {"enter": enter, "stop": stop, "target": target},
            schema={"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64},
        )
