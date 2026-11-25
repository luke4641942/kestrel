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
from kestrel.strategies.conditions import Waiting, above_average, above_past

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

    def waiting(self, bars: pl.DataFrame) -> Waiting | None:
        """Its two tests at the latest close (kestrel.strategies.conditions); in the market
        when both hold."""
        if bars.is_empty() or bars[BTC_CLOSE].null_count():
            return None
        coin = self.symbol.removesuffix("USDT")
        tests = [
            above_average(
                f"BTC above its {self.btc_days}-day average", "BTCUSDT",
                bars[BTC_CLOSE].to_list(), self.btc_days,
            ),
            above_past(
                f"{coin} up over {self.coin_days} days", self.symbol,
                bars["close"].to_list(), self.coin_days,
            ),
        ]  # fmt: skip
        if any(t is None for t in tests):
            return None
        return Waiting(bars["open_time"][-1], "all", [t for t in tests if t is not None])


class BtcRegimeAblation(BtcRegimeTrend):
    """btc-regime-trend with each of its three parts switchable, for the ablation note
    (which part carries the result?). With every switch on (the defaults) it is exactly
    btc-regime-trend; a test checks that. Off means:

    - ``btc_filter`` 0: BTC's regime is ignored (the test always holds);
    - ``coin_trend`` 0: the coin's own trend is ignored;
    - ``vol_sizing`` 0: in the market means fully in (weight 1), not sized to volatility.

    Both filters off with sizing on is vol-hold: always in, sized to volatility.
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        btc_days: int = 50,
        coin_days: int = 30,
        vol_days: int = 30,
        target_vol_pct: int = 50,
        btc_filter: int = 1,
        coin_trend: int = 1,
        vol_sizing: int = 1,
    ) -> None:
        super().__init__(symbol, btc_days, coin_days, vol_days, target_vol_pct)
        self.btc_filter, self.coin_trend, self.vol_sizing = btc_filter, coin_trend, vol_sizing
        parts = [
            "BTC regime" if btc_filter else "no BTC regime",
            "coin trend" if coin_trend else "no coin trend",
            "vol sizing" if vol_sizing else "fully in",
        ]
        self.name = f"ablation: {', '.join(parts)} {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        btc, close = pl.col(BTC_CLOSE), pl.col("close")
        vol = close.log().diff().rolling_std(self.vol_days, min_samples=self.vol_days) * math.sqrt(
            DAYS_PER_YEAR
        )
        regime = btc > btc.rolling_mean(self.btc_days, min_samples=self.btc_days)
        trend = close > close.shift(self.coin_days)
        on = (regime if self.btc_filter else pl.lit(True)) & (
            trend if self.coin_trend else pl.lit(True)
        )
        if self.vol_sizing:
            size = (
                pl.when(vol.is_null() | (vol <= 0))
                .then(0.0)
                .otherwise(pl.min_horizontal(pl.lit(1.0), pl.lit(self.target_vol_pct / 100) / vol))
            )
        else:
            # Fully in, but only once the same warm-up as the sized rule has passed, so every
            # variant starts trading on the same day.
            size = pl.when(vol.is_null()).then(0.0).otherwise(1.0)
        # Every variant waits for the full rule's warm-up (BTC's average and the coin's
        # lookback), so the variants are compared over the same days.
        ready = btc.rolling_mean(self.btc_days, min_samples=self.btc_days).is_not_null() & (
            close.shift(self.coin_days).is_not_null()
        )
        held = on.fill_null(False) & ready
        weight = pl.when(held).then(size).otherwise(0.0).fill_null(0.0).fill_nan(0.0)
        return bars.select(weight.clip(0.0, 1.0).alias("weight"))["weight"]
