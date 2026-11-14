"""Bollinger squeeze breakout (research, YouTube batch 3 G4, Rayner Teo): quiet, then a break.

Volatility clusters: a long quiet stretch tends to end in a large move (the same clustering
the regime model sees). The squeeze says *when*; the 200-day average picks the direction,
and long-only spot can only take the upward one.

Daily bars. Band width is (upper - lower) / middle of the 20-day, 2 SD Bollinger band.
A squeeze is on when the width was at its lowest of the last ``squeeze_bars`` bars at some
point in the last ``recent_bars`` bars. Enter (weight 1) at a close above the upper band
while a squeeze is on and the close is above its ``trend_days``-day average. Exit to cash
at a close below the 20-day average (the band's middle). Long only, fully in or out.

Rayner gives the ideas, not a system; the 120-bar squeeze, the upper-band entry with the
200-day direction and the 20-day trailing exit are the spec's defaults. ``recent_bars``
(20, one band length) is Kestrel's: the spec doesn't say how soon after the squeeze the
break must come.
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl

from kestrel.indicators import bollinger, sma

BAND_BARS = 20


class SqueezeBreakout:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        squeeze_bars: int = 120,
        recent_bars: int = 20,
        trend_days: int = 200,
    ) -> None:
        self.symbol = symbol
        self.squeeze_bars, self.recent_bars, self.trend_days = squeeze_bars, recent_bars, trend_days
        self.name = f"squeeze breakout {squeeze_bars}/{recent_bars} {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        if bars.height >= 2 and bars["open_time"].diff().median() != timedelta(days=1):
            raise ValueError(f"{self.name} needs daily bars")
        close = bars["close"]
        bands = bollinger(close, BAND_BARS, 2.0)
        width = ((bands.upper - bands.lower) / bands.middle).to_list()
        upper, middle = bands.upper.to_list(), bands.middle.to_list()
        trend = sma(close, self.trend_days).to_list()
        closes = close.to_list()
        out: list[float] = []
        held = False
        last_squeeze: int | None = None
        for t, c in enumerate(closes):
            window = width[max(0, t - self.squeeze_bars + 1) : t + 1]
            known = [w for w in window if w is not None]
            if len(known) == self.squeeze_bars and known[-1] <= min(known):  # a full window
                last_squeeze = t
            if held:
                held = not (middle[t] is not None and c < middle[t])
            elif (
                last_squeeze is not None
                and t - last_squeeze < self.recent_bars
                and upper[t] is not None
                and trend[t] is not None
                and c > upper[t]
                and c > trend[t]
            ):
                held = True
            out.append(1.0 if held else 0.0)
        return pl.Series("weight", out, dtype=pl.Float64)
