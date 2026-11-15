"""Hold while new money arrives (research, 2026-11-15): the stablecoin study as a strategy.

The study (note does-stablecoin-supply-growth-lead-prices) found weeks that start after 4
weeks of stablecoin supply growth beat weeks that start after shrinkage (PROCEED, P 91%).
This trades it exactly as measured: at each Sunday's daily close (UTC), hold the coin for
the next week if ``stablecoins_up`` (kestrel.data.features) is 1, else cash. Unknown
(before the supply history) is cash. Fully in or out; long only.
"""

from __future__ import annotations

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
