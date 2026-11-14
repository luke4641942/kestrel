"""Riding volatile spells in MACD's direction (research).

The volatile-spell direction study (kestrel.studies.volatile_direction, note
which-way-do-volatile-spells-go-macd) found that on days the regime model calls volatile,
the week after a positive MACD 12/26/9 histogram returned +1.63% on average and the week
after a negative one -2.00% (BTC and ETH, 2021-25): in volatile spells the direction
continued. Trend-vol-target holds little in those spells, so this is the part it leaves.

At a day's close, if the regime model's filtered state is volatile (``regime_volatile``) and
the MACD histogram is above zero, the coin is held fully for the next ``hold_days`` days,
the study's horizon: bought at the next open, held through the decisions of those days.
At the end of the week the rule decides again: if both still hold, it stays in (no trade);
otherwise it goes to cash. Signals during a hold are ignored, as the study's
non-overlapping weeks were. No regime state yet means no entry. Long only, daily bars.
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl

from kestrel.data.features import REGIME_VOLATILE
from kestrel.indicators import macd


class VolatileMacd:
    def __init__(self, symbol: str = "BTCUSDT", hold_days: int = 7) -> None:
        if hold_days < 1:
            raise ValueError("hold_days must be at least 1")
        self.symbol = symbol
        self.hold_days = hold_days
        self.name = f"volatile-spell MACD {symbol}, {hold_days}-day holds"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        if bars.height < 2:
            return pl.Series("weight", [0.0] * bars.height, dtype=pl.Float64)
        if bars["open_time"].diff().median() != timedelta(days=1):
            raise ValueError(f"{self.name} needs daily bars")
        if REGIME_VOLATILE not in bars.columns:
            raise ValueError(f"{self.name} needs the {REGIME_VOLATILE} feature")
        hist = macd(bars["close"]).histogram.to_list()
        volatile = bars[REGIME_VOLATILE].to_list()
        weights: list[float] = []
        held_until = -1  # the last day index whose decision is "hold"
        for i, (h, v) in enumerate(zip(hist, volatile, strict=True)):
            if i > held_until and v == 1.0 and h is not None and h > 0:
                held_until = i + self.hold_days - 1
            weights.append(1.0 if i <= held_until else 0.0)
        return pl.Series("weight", weights, dtype=pl.Float64)
