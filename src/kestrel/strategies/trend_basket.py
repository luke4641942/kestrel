"""Trend following across a basket of large coins (research).

Trend-vol-target's rule, applied to each coin of the weekly large-coin basket
(kestrel.strategies.basket: the ``top_n`` coins by 30-day median volume with enough
history) instead of to BTC alone. Diversifying one trend rule across many markets is
where most of trend following's Sharpe comes from in the futures literature (Moskowitz,
Ooi and Pedersen, 2012); this tests whether that carries over to crypto, and whether
trend works beyond BTC at all.

Every Sunday's daily close (UTC) is a decision. Each basket coin gets a slot of
``1 / top_n`` of equity, filled in proportion to its own trend and volatility, exactly as
trend-vol-target sizes BTC:

    weight = (1 / top_n) x (average of three votes: close above the close 20, 60 and
             120 days earlier) x min(1, target_vol / the coin's annualised volatility of
             its last 30 daily log returns)

So every slot carries at most the same risk, and a coin whose trends disagree, or that is
wilder than the target, fills less of its slot; the rest is cash. Weekly rather than daily
decisions: at a few hundred dollars, ten slots of a few dollars' change each would mostly
be below the exchange's minimum order.
"""

from __future__ import annotations

import math

import polars as pl

from kestrel.strategies.basket import as_decisions, no_decisions, require_daily, weekly_basket

DAYS_PER_YEAR = 365


class TrendBasket:
    def __init__(
        self,
        symbol: str = "UNIVERSE",
        top_n: int = 10,
        min_history_days: int = 180,
        min_volume_musd: int = 20,
        target_vol_pct: int = 50,
    ) -> None:
        self.symbol = symbol
        self.top_n, self.min_history, self.min_volume = top_n, min_history_days, min_volume_musd
        self.target_vol = target_vol_pct / 100
        self.lookbacks = (20, 60, 120)
        self.vol_days = 30
        self.name = f"trend 20/60/120d across top {top_n} coins, vol target {target_vol_pct}%"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        if panel.is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        close = pl.col("close")
        votes = {f"_v{d}": (close > close.shift(d)).cast(pl.Float64) for d in self.lookbacks}
        log_return = (close / close.shift(1)).log()
        columns = {
            **votes,
            "_vol": log_return.rolling_std(self.vol_days) * math.sqrt(DAYS_PER_YEAR),
            "_ready": close.shift(max(self.lookbacks)).is_not_null(),
        }
        basket = weekly_basket(panel, self.top_n, self.min_history, self.min_volume, columns)
        trend = pl.mean_horizontal(list(votes))
        size = (self.target_vol / pl.col("_vol")).clip(upper_bound=1.0)
        weight = (trend * size / self.top_n).fill_nan(0.0).fill_null(0.0)
        return as_decisions(
            panel,
            basket.with_columns(
                weight=pl.when(pl.col("_ready") & (pl.col("_vol") > 0)).then(weight).otherwise(0.0)
            ),
        )
