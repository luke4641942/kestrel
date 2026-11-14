"""Trend-vol-target with a regime filter (research, backlog S2).

Trend rules lose most in panicky, high-volatility markets that whip back and forth: Daniel
and Moskowitz (Momentum Crashes, 2016) find momentum crashes cluster in high-volatility
"panic" states and are partly forecastable. This asks whether stepping aside in those spells
helps the one strategy on pursue.

The weight is trend-vol-target's, set to 0 on any day the regime model's most likely state
is its most volatile one (the ``regime_volatile`` feature, kestrel.data.features: filtered,
from monthly refits on earlier data only). A day with no regime state keeps the plain
weight. ``regime_filter`` 0 turns the filter off, so the plain rule runs under the same note
and on the same bars as its comparison.

The filter only ever lowers a weight, and it reacts to a state that persists (the volatile
state's typical spell is weeks), not to one day's fall: the crash brake, which sold on a
single close, made both survivors worse.
"""

from __future__ import annotations

import polars as pl

from kestrel.data.features import REGIME_VOLATILE
from kestrel.strategies.trend import TrendVolTarget


class TrendVolTargetRegime(TrendVolTarget):
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        fast_days: int = 20,
        mid_days: int = 60,
        slow_days: int = 120,
        vol_days: int = 30,
        target_vol_pct: int = 50,
        regime_filter: int = 1,
    ) -> None:
        if regime_filter not in (0, 1):
            raise ValueError("regime_filter is 1 (on) or 0 (off)")
        super().__init__(symbol, fast_days, mid_days, slow_days, vol_days, target_vol_pct)
        self.filtered = bool(regime_filter)
        self.name = (
            f"{self.name}, {'out in the volatile regime' if self.filtered else 'no regime filter'}"
        )

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        base = super().target_weights(bars)
        if not self.filtered:
            return base
        if REGIME_VOLATILE not in bars.columns:
            raise ValueError(f"{self.name} needs the {REGIME_VOLATILE} feature")
        volatile = bars[REGIME_VOLATILE].fill_null(0.0) > 0
        return pl.select(pl.when(volatile).then(0.0).otherwise(base).alias("weight"))["weight"]
