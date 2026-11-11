"""Cross-sectional momentum with an absolute filter (research).

Every Sunday's daily close (UTC) is a decision. A coin is eligible if, using only bars
up to that close and within one unbroken history (a gap starts a new one; see
kestrel.data.panel):

- it has at least ``min_history_days`` days of history, and
- its median daily quote volume over the last 30 days is at least ``min_volume_musd``
  million USDT.

Eligible coins are scored by their average rank on three trailing returns (default 7,
14 and 28 days): averaging three lookbacks avoids choosing one. The ``top_n`` best are
selected, each at ``1 / top_n`` of equity, and then the absolute filter applies: a selected
coin whose own ``slow_days`` return is not positive leaves its slot in cash (dual
momentum: rank first, then require the coin's own trend to be up). When the whole market
falls, the portfolio moves to cash instead of holding the least-bad losers.

Daily bars only, with the coin universe from kestrel.data.universe (delisted coins
included, so the ranking can pick coins that later collapsed, as it could have then).
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl

VOLUME_DAYS = 30


class CrossSectionalMomentum:
    def __init__(
        self,
        symbol: str = "UNIVERSE",
        top_n: int = 5,
        fast_days: int = 7,
        mid_days: int = 14,
        slow_days: int = 28,
        min_history_days: int = 180,
        min_volume_musd: int = 20,
    ) -> None:
        if not fast_days < mid_days < slow_days:
            raise ValueError("lookbacks must increase: fast_days < mid_days < slow_days")
        self.symbol = symbol
        self.top_n = top_n
        self.lookbacks = (fast_days, mid_days, slow_days)
        self.min_history = min_history_days
        self.min_volume = min_volume_musd * 1e6
        self.name = (
            f"momentum top {top_n} of coins over {min_volume_musd}M/day, "
            f"{fast_days}/{mid_days}/{slow_days}d"
        )

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        empty = pl.DataFrame(
            schema={"open_time": pl.Datetime("us", "UTC"), "symbol": pl.Utf8, "weight": pl.Float64}
        )
        if panel.is_empty():
            return empty
        step = panel["open_time"].unique().sort().diff().median()
        if step != timedelta(days=1):
            raise ValueError(f"{self.name} needs daily bars")
        by = ["symbol", "history"]
        close = pl.col("close")
        fast, mid, slow = self.lookbacks
        scored = (
            panel.with_columns(
                age=pl.int_range(1, pl.len() + 1).over(by),
                volume=pl.col("quote_volume")
                .rolling_median(VOLUME_DAYS, min_samples=VOLUME_DAYS)
                .over(by),
                **{f"r{d}": (close / close.shift(d)).over(by) - 1 for d in (fast, mid, slow)},
            )
            .filter(
                (pl.col("open_time").dt.weekday() == 7)
                & (pl.col("age") >= self.min_history)
                & (pl.col("volume") >= self.min_volume)
                & pl.all_horizontal(pl.col(f"r{d}").is_not_null() for d in (fast, mid, slow))
            )
            .with_columns(
                score=pl.mean_horizontal(
                    pl.col(f"r{d}").rank().over("open_time") for d in (fast, mid, slow)
                )
            )
            .sort(["open_time", "score", "symbol"], descending=[False, True, False])
            .with_columns(place=pl.int_range(pl.len()).over("open_time"))
        )
        chosen = scored.filter((pl.col("place") < self.top_n) & (pl.col(f"r{slow}") > 0)).select(
            "open_time", "symbol", weight=pl.lit(1 / self.top_n)
        )
        # Every Sunday is a decision; one that holds nothing is a single null-symbol row.
        sundays = panel.filter(pl.col("open_time").dt.weekday() == 7).select("open_time").unique()
        idle = sundays.join(chosen, on="open_time", how="anti").select(
            "open_time", symbol=pl.lit(None, pl.Utf8), weight=pl.lit(0.0)
        )
        return pl.concat([chosen, idle]).sort("open_time", "symbol", nulls_last=True)
