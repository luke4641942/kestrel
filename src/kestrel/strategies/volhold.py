"""Volatility-managed holding (research): trend-vol-target with the trend taken out.

Holds the coin sized by ``min(1, target_vol / volatility)``, where volatility is the
annualised standard deviation of the last ``vol_days`` daily log returns: fully in when
the market is calmer than the target, less when it is wilder. No trend rule at all.

Taking less risk when recent volatility is high improved Sharpe in equities (Moreira and
Muir, 2017). Here it is mainly an attribution test. Trend-vol-target multiplies a trend
score by exactly this size; with the same defaults (30 days, 50% target), comparing the
two shows which half earns its result. Cash until the volatility window has filled.

Lookbacks are in days and converted to bars from the data's own bar spacing, as in
trend-vol-target.
"""

from __future__ import annotations

import math
from datetime import timedelta

import polars as pl

DAYS_PER_YEAR = 365


class VolManagedHold:
    def __init__(self, symbol: str = "BTCUSDT", vol_days: int = 30, target_vol_pct: int = 50):
        self.symbol = symbol
        self.vol_days = vol_days
        self.target_vol = target_vol_pct / 100
        self.name = f"hold, vol target {target_vol_pct}% over {vol_days}d {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        if bars.height < 2:
            return pl.Series("weight", [0.0] * bars.height, dtype=pl.Float64)
        step = bars["open_time"].diff().median()
        assert isinstance(step, timedelta)
        per_day = timedelta(days=1) / step
        window = max(2, round(self.vol_days * per_day))
        log_return = (pl.col("close") / pl.col("close").shift(1)).log()
        vol = log_return.rolling_std(window) * math.sqrt(DAYS_PER_YEAR * per_day)
        size = (self.target_vol / vol).clip(upper_bound=1.0)
        return bars.select(weight=size.fill_nan(0.0).fill_null(0.0))["weight"]
