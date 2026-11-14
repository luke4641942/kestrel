"""Monthly momentum rotation (research, YouTube batch 3 G3 with batch 2 A6): hold the
strongest large coins that are in an uptrend, or cash.

Rayner Teo's ETF version ranks gold, stocks, bonds and commodities by their 12-month
return each month and holds the top two above their 200-day averages. Here the assets are
BTC, ETH and SOL. Time-series momentum in crypto (Liu and Tsyvinski 2021) is the reason
it might work; the 200-day filter is the absolute-momentum half of dual momentum.

Daily bars, decided at the close of each month's last day (UTC) and filled at the first
day's open. A coin is eligible if it has ``lookback_days`` of unbroken history and its
close is above its ``trend_days``-day average. Eligible coins are ranked by their
``lookback_days`` return; the top ``top_n`` each get ``1 / top_n`` of equity, and an
unfilled slot stays in cash. Positions only change at month ends. A coin listed later
(SOL in 2020-08) joins the ranking once it has the history.
"""

from __future__ import annotations

import polars as pl

from kestrel.strategies.basket import BY, DECISIONS, no_decisions, require_daily

COINS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


class MonthlyRotation:
    def __init__(
        self,
        symbol: str = "UNIVERSE",
        lookback_days: int = 365,
        trend_days: int = 200,
        top_n: int = 2,
    ) -> None:
        self.symbol = symbol
        self.lookback, self.trend, self.top_n = lookback_days, trend_days, top_n
        self.name = f"BTC/ETH/SOL monthly rotation, top {top_n} by {lookback_days}d"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        coins = panel.filter(pl.col("symbol").is_in(COINS))
        if coins.is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        close = pl.col("close")
        month_end = (pl.col("open_time") + pl.duration(days=1)).dt.day() == 1
        scored = coins.with_columns(
            _r=(close / close.shift(self.lookback)).over(BY) - 1,
            _ma=close.rolling_mean(self.trend).over(BY),
        ).filter(month_end)
        chosen = (
            scored.filter(pl.col("_r").is_not_null() & (close > pl.col("_ma")))
            .sort(["open_time", "_r", "symbol"], descending=[False, True, False])
            .group_by("open_time", maintain_order=True)
            .head(self.top_n)
            .with_columns(weight=pl.lit(1 / self.top_n))
            .select(list(DECISIONS))
        )
        ends = panel.filter(month_end).select("open_time").unique()
        idle = ends.join(chosen, on="open_time", how="anti").select(
            "open_time", symbol=pl.lit(None, pl.Utf8), weight=pl.lit(0.0)
        )
        out = pl.concat([chosen, idle]).select([pl.col(c).cast(t) for c, t in DECISIONS.items()])
        return out.sort("open_time", "symbol", nulls_last=True)
