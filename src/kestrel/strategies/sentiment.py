"""Fear & Greed contrarian (research): buy when the crowd is frightened, sell when it's greedy.

Uses the Crypto Fear & Greed Index (kestrel.data.sentiment), attached to each bar as the
latest value known by that bar's close. In cash, it buys when the index is at or below
``fear_max`` (the index's own "Extreme Fear" band is 25 and below); holding, it sells when
the index is at or above ``greed_min`` ("Extreme Greed" is 76 and above). In between it
keeps whatever it holds. An unknown reading (a gap in the published series) changes
nothing. Fully in or fully out: long only, spot.

The thresholds are the index's published band edges, not values fitted to returns.
"""

from __future__ import annotations

import polars as pl

from kestrel.data.sentiment import FEATURE


class FearGreedContrarian:
    def __init__(self, symbol: str = "BTCUSDT", fear_max: int = 25, greed_min: int = 76) -> None:
        if not fear_max < greed_min:
            raise ValueError("fear_max must be below greed_min")
        self.symbol = symbol
        self.fear_max, self.greed_min = fear_max, greed_min
        self.name = f"fear & greed contrarian {fear_max}/{greed_min} {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        if FEATURE not in bars.columns:
            raise ValueError(f"{self.name} needs the {FEATURE} feature attached to its bars")
        holding = False
        weights = []
        for reading in bars[FEATURE].to_list():
            if reading is not None:
                if not holding and reading <= self.fear_max:
                    holding = True
                elif holding and reading >= self.greed_min:
                    holding = False
            weights.append(1.0 if holding else 0.0)
        return pl.Series("weight", weights, dtype=pl.Float64)
