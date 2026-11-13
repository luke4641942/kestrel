"""One-week time-series momentum (research): hold next week if this week rose.

Liu and Tsyvinski (Risks and Returns of Cryptocurrency, NBER working paper 24877) report
significant time-series momentum in crypto at daily and weekly horizons, much shorter
than trend-vol-target's 20 to 120 days. This tests the weekly horizon in its plainest
form.

Daily bars. At each Sunday's close (UTC), the week's return is the close over the close
7 days earlier. If it is positive, the target weight is 1 until the next Sunday's close;
otherwise 0. Cash until the first Sunday with a week of history. Fully in or fully out;
long only, spot. There is no setting to choose: the week is the horizon being tested.
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl


class WeeklyMomentum:
    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self.symbol = symbol
        self.name = f"weekly momentum {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        if bars.height < 2:
            return pl.Series("weight", [0.0] * bars.height, dtype=pl.Float64)
        if bars["open_time"].diff().median() != timedelta(days=1):
            raise ValueError(f"{self.name} needs daily bars")
        close = pl.col("close")
        # The close 7 days earlier by time, not by row, so a missing bar can't stretch a week.
        week_ago = bars.select(
            "open_time", _then=pl.col("close"), _at=pl.col("open_time") + timedelta(days=7)
        )
        decided = (
            bars.join(week_ago.drop("open_time"), left_on="open_time", right_on="_at", how="left")
            .sort("open_time")
            .select(
                pl.when(pl.col("open_time").dt.weekday() == 7)
                .then((close > pl.col("_then")).cast(pl.Float64).fill_null(0.0))
                .otherwise(None)
                .forward_fill()
                .fill_null(0.0)
                .alias("weight")
            )
        )
        return decided["weight"]
