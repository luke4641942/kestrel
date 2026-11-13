"""Short-term reversal after big down days (research): buy a sharp one-day drop, hold briefly.

A down day is "big" when its close-to-close log return is below ``-drop_sigma`` times the
standard deviation of the ``vol_days`` daily log returns before it. The day's own return
is left out of that window, so a crash can't widen the yardstick it is measured against.
Everything used is known at the drop day's close.

After a big down day the strategy holds fully for ``hold_days`` bars: target weight 1 at
the drop's close and the next ``hold_days - 1`` closes, so it buys at the next open and
sells ``hold_days`` opens later. Another big down day while holding restarts the clock.
Cash until the volatility window has filled. Long only, spot.

Daily bars: the defaults (2 standard deviations, 30 days, 3 days held) are round, common
choices set before any run, not values fitted to returns.
"""

from __future__ import annotations

import polars as pl


class DropReversal:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        drop_sigma_pct: int = 200,
        vol_days: int = 30,
        hold_days: int = 3,
    ) -> None:
        self.symbol = symbol
        self.drop_sigma = drop_sigma_pct / 100
        self.vol_days, self.hold_days = vol_days, hold_days
        self.name = (
            f"drop reversal {self.drop_sigma:g} sd over {vol_days}d, hold {hold_days}d {symbol}"
        )

    def big_drops(self, bars: pl.DataFrame) -> pl.Series:
        """True on each bar whose close is a big down day (null history counts as no)."""
        log_return = (pl.col("close") / pl.col("close").shift(1)).log()
        prior_vol = log_return.shift(1).rolling_std(self.vol_days)
        drop = (log_return < -self.drop_sigma * prior_vol).fill_null(False)
        return bars.select(drop=drop)["drop"]

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        drops = self.big_drops(bars).cast(pl.Float64)
        holding = drops.rolling_max(self.hold_days, min_samples=1)
        return holding.fill_null(0.0).alias("weight")
