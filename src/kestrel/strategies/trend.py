"""Trend following with volatility targeting (research).

At each bar's close, for each of three lookbacks (default 20, 60 and 120 days), a vote is
1 if the close is above the close that many days earlier. The trend is the average of the
three votes: 0, 1/3, 2/3 or 1. Averaging several lookbacks, rather than choosing one, is
deliberate: picking the best single lookback on past data is how a trend backtest fools
you.

Volatility is the standard deviation of log returns over the last ``vol_days`` days,
annualised. The target weight is ``trend x min(1, target_vol / volatility)``: fully in at
most (spot, no leverage), less when the market is wilder than the target. Cash until the
longest lookback and the volatility window have filled.

Lookbacks are in days and converted to bars from the data's own bar spacing, so the rule
means the same on daily or 4h bars.
"""

from __future__ import annotations

import math
from datetime import timedelta

import polars as pl

DAYS_PER_YEAR = 365


class TrendVolTarget:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        fast_days: int = 20,
        mid_days: int = 60,
        slow_days: int = 120,
        vol_days: int = 30,
        target_vol_pct: int = 50,
    ) -> None:
        if not fast_days < mid_days < slow_days:
            raise ValueError("lookbacks must increase: fast_days < mid_days < slow_days")
        self.symbol = symbol
        self.lookbacks = (fast_days, mid_days, slow_days)
        self.vol_days = vol_days
        self.target_vol = target_vol_pct / 100
        self.name = (
            f"trend {fast_days}/{mid_days}/{slow_days}d, vol target {target_vol_pct}% {symbol}"
        )

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        if bars.height < 2:
            return pl.Series("weight", [0.0] * bars.height, dtype=pl.Float64)
        # Bars per day from the median spacing: robust to the odd missing bar.
        step = bars["open_time"].diff().median()
        assert isinstance(step, timedelta)
        per_day = timedelta(days=1) / step

        def bars_for(days: int) -> int:
            return max(1, round(days * per_day))

        close = pl.col("close")
        votes = [(close > close.shift(bars_for(days))).cast(pl.Float64) for days in self.lookbacks]
        log_return = (close / close.shift(1)).log()
        vol = log_return.rolling_std(bars_for(self.vol_days)) * math.sqrt(DAYS_PER_YEAR * per_day)
        # A shift into the start of the data is null, so the vote is null until it fills.
        ready = close.shift(bars_for(max(self.lookbacks))).is_not_null() & vol.is_not_null()
        weight = (pl.sum_horizontal(votes) / len(votes)) * (self.target_vol / vol).clip(
            upper_bound=1.0
        )
        out = bars.select(
            weight=pl.when(ready).then(weight).otherwise(0.0).fill_nan(0.0).fill_null(0.0)
        )
        return out["weight"]
