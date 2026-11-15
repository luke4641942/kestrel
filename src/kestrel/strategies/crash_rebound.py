"""Altcoin crash rebound (research; a graduate of the exploration experiment,
research/exploration/log.md). Long only, in the reversal family (disclosed in its note).

At a close where the coin is more than ``drop_pct``% below its close ``days`` days
earlier, go fully into it for ``hold_days`` days (that close's decision and the next
``hold_days`` - 1); a new crash inside the window extends it. Otherwise in cash.
"""

from __future__ import annotations

import polars as pl


class CrashRebound:
    def __init__(
        self, symbol: str = "BTCUSDT", drop_pct: int = 30, days: int = 7, hold_days: int = 3
    ) -> None:
        self.symbol = symbol
        self.drop_pct, self.days, self.hold_days = drop_pct, days, hold_days
        self.name = f"crash -{drop_pct}%/{days}d, hold {hold_days}d {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        close = pl.col("close")
        crashed = (close / close.shift(self.days) - 1 < -self.drop_pct / 100).fill_null(False)
        held = crashed.cast(pl.Float64).rolling_max(self.hold_days, min_samples=1)
        return bars.select(held.fill_null(0.0).alias("weight"))["weight"]
