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

With ``macd_filter`` 1, a coin is also only eligible while its daily MACD (12, 26, 9)
histogram is above zero at the month-end close (Luke's 2026-11-15 variant).

``RotationWithStablecoins`` adds one condition at each month end: hold nothing unless the
stablecoin supply grew over the last 4 weeks (``stablecoins_up``, kestrel.data.features).

``BtcGoldRotation`` is the same rule over BTC and gold (PAXGUSDT, one token per troy
ounce): assets that don't move together, which is what rotation is meant to exploit.
"""

from __future__ import annotations

import polars as pl

from kestrel.data.stablecoins import FEATURE
from kestrel.indicators import macd
from kestrel.strategies.basket import BY, DECISIONS, no_decisions, require_daily

COINS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


class MonthlyRotation:
    coins: tuple[str, ...] = COINS
    label = "BTC/ETH/SOL"

    def __init__(
        self,
        symbol: str = "UNIVERSE",
        lookback_days: int = 365,
        trend_days: int = 200,
        top_n: int = 2,
        macd_filter: int = 0,
    ) -> None:
        self.symbol = symbol
        self.lookback, self.trend, self.top_n = lookback_days, trend_days, top_n
        self.macd_filter = bool(macd_filter)
        self.name = f"{self.label} monthly rotation, top {top_n} by {lookback_days}d" + (
            ", MACD filter" if macd_filter else ""
        )

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        coins = panel.filter(pl.col("symbol").is_in(self.coins))
        if coins.is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        if self.macd_filter:
            coins = pl.concat(
                c.with_columns(_hist=macd(c["close"]).histogram)
                for c in coins.sort("open_time").partition_by(BY)
            )
        else:
            coins = coins.with_columns(_hist=pl.lit(1.0))
        close = pl.col("close")
        month_end = (pl.col("open_time") + pl.duration(days=1)).dt.day() == 1
        scored = coins.with_columns(
            _r=(close / close.shift(self.lookback)).over(BY) - 1,
            _ma=close.rolling_mean(self.trend).over(BY),
        ).filter(month_end)
        chosen = (
            scored.filter(
                pl.col("_r").is_not_null() & (close > pl.col("_ma")) & (pl.col("_hist") > 0)
            )
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


class BtcGoldRotation(MonthlyRotation):
    coins = ("BTCUSDT", "PAXGUSDT")
    label = "BTC/gold"

    def __init__(
        self,
        symbol: str = "UNIVERSE",
        lookback_days: int = 365,
        trend_days: int = 200,
        top_n: int = 1,
        macd_filter: int = 0,
    ) -> None:
        super().__init__(symbol, lookback_days, trend_days, top_n, macd_filter)


class RotationWithStablecoins(MonthlyRotation):
    """BTC/ETH/SOL rotation that holds cash at month ends when stablecoin supply shrank."""

    label = "BTC/ETH/SOL"

    def __init__(
        self,
        symbol: str = "UNIVERSE",
        lookback_days: int = 180,
        trend_days: int = 200,
        top_n: int = 2,
    ) -> None:
        super().__init__(symbol, lookback_days, trend_days, top_n, 0)
        self.name += ", stablecoin filter"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        if FEATURE not in panel.columns:
            raise ValueError(f"{self.name} needs the {FEATURE} feature attached to its panel")
        decisions = super().target_weights(panel)
        # Month ends whose supply didn't grow (or is unknown) hold nothing.
        growing = panel.filter(pl.col(FEATURE) == 1.0).select("open_time").unique()
        kept = decisions.join(growing, on="open_time", how="semi")
        cash = (
            decisions.join(growing, on="open_time", how="anti")
            .select("open_time")
            .unique()
            .with_columns(symbol=pl.lit(None, pl.Utf8), weight=pl.lit(0.0))
        )
        return (
            pl.concat([kept, cash.select(kept.columns)])
            .select([pl.col(c).cast(t) for c, t in DECISIONS.items()])
            .sort("open_time", "symbol", nulls_last=True)
        )
