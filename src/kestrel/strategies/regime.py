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

``TrendVolTargetRegime4h`` is the same rule reading the 4h model instead
(``regime_volatile_4h``: the state of the 4h bar that closes at the day's close). Findings
23 found it turns volatile a median ~40 hours before the daily model; its note asks
whether that earlier exit is worth its false alarms (3 in 4 of its spells).
"""

from __future__ import annotations

import polars as pl

from kestrel.data.features import REGIME_VOLATILE, REGIME_VOLATILE_4H
from kestrel.strategies.trend import TrendVolTarget


class TrendVolTargetRegime(TrendVolTarget):
    feature = REGIME_VOLATILE
    label = "out in the volatile regime"

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
        self.name = f"{self.name}, {self.label if self.filtered else 'no regime filter'}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        base = super().target_weights(bars)
        if not self.filtered:
            return base
        if self.feature not in bars.columns:
            raise ValueError(f"{self.name} needs the {self.feature} feature")
        volatile = bars[self.feature].fill_null(0.0) > 0
        return pl.select(pl.when(volatile).then(0.0).otherwise(base).alias("weight"))["weight"]


class TrendVolTargetRegime4h(TrendVolTargetRegime):
    feature = REGIME_VOLATILE_4H
    label = "out in the 4h model's volatile regime"
