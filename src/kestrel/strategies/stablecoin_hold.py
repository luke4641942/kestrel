"""Hold while new money arrives (research, 2026-11-15): the stablecoin study as a strategy.

The study (note does-stablecoin-supply-growth-lead-prices) found weeks that start after 4
weeks of stablecoin supply growth beat weeks that start after shrinkage (PROCEED, P 91%).
This trades it exactly as measured: at each Sunday's daily close (UTC), hold the coin for
the next week if ``stablecoins_up`` (kestrel.data.features) is 1, else cash. Unknown
(before the supply history) is cash. Fully in or out; long only.

``StablecoinVolHold`` sizes the same on/off by volatility, as trend-vol-target does: while
the signal is on, the weight is ``min(1, target_vol / volatility)`` over the last
``vol_days`` days, so it holds less when the market is wild (the plain hold's weakness was
taking every crash in full).
"""

from __future__ import annotations

import math
from datetime import timedelta

import polars as pl

from kestrel.data.stablecoins import FEATURE


class StablecoinHold:
    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self.symbol = symbol
        self.name = f"hold while stablecoins grow {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        if FEATURE not in bars.columns:
            raise ValueError(f"{self.name} needs the {FEATURE} feature attached to its bars")
        if bars.height >= 2 and bars["open_time"].diff().median() != timedelta(days=1):
            raise ValueError(f"{self.name} needs daily bars")
        weights = bars.select(
            pl.when(pl.col("open_time").dt.weekday() == 7)
            .then((pl.col(FEATURE) == 1.0).fill_null(False).cast(pl.Float64))
            .otherwise(None)
            .forward_fill()
            .fill_null(0.0)
            .alias("weight")
        )
        return weights["weight"]


class StablecoinVolHold(StablecoinHold):
    def __init__(
        self, symbol: str = "BTCUSDT", vol_days: int = 30, target_vol_pct: int = 50
    ) -> None:
        super().__init__(symbol)
        self.vol_days, self.target_vol = vol_days, target_vol_pct / 100
        self.name = f"hold while stablecoins grow, vol target {target_vol_pct}% {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        on = super().target_weights(bars)
        close = pl.col("close")
        vol = (close / close.shift(1)).log().rolling_std(self.vol_days) * math.sqrt(365)
        size = bars.select(
            (self.target_vol / vol).clip(upper_bound=1.0).fill_nan(0.0).fill_null(0.0).alias("s")
        )["s"]
        return (on * size).alias("weight")
