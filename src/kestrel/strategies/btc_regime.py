"""BTC regime + the coin's own trend, sized by volatility (research; a graduate of the
exploration experiment, research/exploration/log.md).

Each day at the close, for one altcoin:

- in the market only while BTC closes above its ``btc_days``-day average (``btc_close``,
  BTC's close on the same bar, kestrel.data.features) and the coin closes above its close
  ``coin_days`` days earlier;
- then sized to ``target_vol_pct`` / the coin's annualised volatility of its last
  ``vol_days`` daily log returns, capped at 100%; otherwise in cash.

A day with no BTC bar, or before the averages and volatility are known, holds cash.
"""

from __future__ import annotations

import math

import polars as pl

from kestrel.data.features import BTC_CLOSE

DAYS_PER_YEAR = 365


class BtcRegimeTrend:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        btc_days: int = 50,
        coin_days: int = 30,
        vol_days: int = 30,
        target_vol_pct: int = 50,
    ) -> None:
        self.symbol = symbol
        self.btc_days, self.coin_days = btc_days, coin_days
        self.vol_days, self.target_vol_pct = vol_days, target_vol_pct
        self.name = f"BTC>{btc_days}d & up {coin_days}d, vol {target_vol_pct}% {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        btc, close = pl.col(BTC_CLOSE), pl.col("close")
        vol = close.log().diff().rolling_std(self.vol_days, min_samples=self.vol_days) * math.sqrt(
            DAYS_PER_YEAR
        )
        on = (btc > btc.rolling_mean(self.btc_days, min_samples=self.btc_days)) & (
            close > close.shift(self.coin_days)
        )
        # min_horizontal skips nulls, so an unknown volatility is handled first: cash.
        size = (
            pl.when(vol.is_null() | (vol <= 0))
            .then(0.0)
            .otherwise(pl.min_horizontal(pl.lit(1.0), pl.lit(self.target_vol_pct / 100) / vol))
        )
        weight = pl.when(on.fill_null(False)).then(size).otherwise(0.0).fill_null(0.0).fill_nan(0.0)
        return bars.select(weight.clip(0.0, 1.0).alias("weight"))["weight"]
