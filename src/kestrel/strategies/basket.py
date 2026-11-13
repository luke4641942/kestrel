"""Large-coin baskets: the shared eligibility rule, and the plain basket as a control.

Every Sunday's daily close (UTC) is a decision. Using only bars up to that close and
within one unbroken history (a gap starts a new one; see kestrel.data.panel), a coin is
eligible when it has at least ``min_history_days`` days of history and a median daily
quote volume over the last 30 days of at least ``min_volume_musd`` million USDT. The
``top_n`` eligible coins with the highest median volume form the week's basket: "large"
is measured by trading volume, since market capitalisation isn't in the data. The universe
includes delisted coins, so a basket can hold coins that later collapsed, as it could have
then.

``LargeCoinBasket`` holds the whole basket in equal weights, ``1 / top_n`` each; while
fewer than ``top_n`` coins are eligible, the empty slots stay in cash. It is the control the basket
strategies (trend across coins, nearness to the 52-week high) must beat: if they don't,
their signal adds nothing to simply holding large coins.
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl

VOLUME_DAYS = 30
BY = ["symbol", "history"]
DECISIONS: dict[str, pl.DataType] = {
    "open_time": pl.Datetime("us", "UTC"),
    "symbol": pl.Utf8(),
    "weight": pl.Float64(),
}


def require_daily(panel: pl.DataFrame, name: str) -> None:
    step = panel["open_time"].unique().sort().diff().median()
    if step != timedelta(days=1):
        raise ValueError(f"{name} needs daily bars")


def weekly_basket(
    panel: pl.DataFrame,
    top_n: int,
    min_history_days: int,
    min_volume_musd: int,
    columns: dict[str, pl.Expr] | None = None,
) -> pl.DataFrame:
    """Each Sunday's basket: open_time, symbol, history, plus ``columns`` (expressions
    computed per coin history over all its bars, so lookbacks see the days before)."""
    with_cols = panel.with_columns(
        _age=pl.int_range(1, pl.len() + 1).over(BY),
        _volume=pl.col("quote_volume")
        .rolling_median(VOLUME_DAYS, min_samples=VOLUME_DAYS)
        .over(BY),
        **{name: expr.over(BY) for name, expr in (columns or {}).items()},
    )
    return (
        with_cols.filter(
            (pl.col("open_time").dt.weekday() == 7)
            & (pl.col("_age") >= min_history_days)
            & (pl.col("_volume") >= min_volume_musd * 1e6)
        )
        .sort(["open_time", "_volume", "symbol"], descending=[False, True, False])
        .with_columns(_place=pl.int_range(pl.len()).over("open_time"))
        .filter(pl.col("_place") < top_n)
        .drop("_age", "_volume", "_place")
    )


def as_decisions(panel: pl.DataFrame, chosen: pl.DataFrame) -> pl.DataFrame:
    """Chosen (open_time, symbol, weight) rows with weight > 0, plus a null-symbol row for
    every Sunday that holds nothing: each Sunday is a decision."""
    held = chosen.filter(pl.col("weight") > 0).select(list(DECISIONS))
    sundays = panel.filter(pl.col("open_time").dt.weekday() == 7).select("open_time").unique()
    idle = sundays.join(held, on="open_time", how="anti").select(
        "open_time", symbol=pl.lit(None, pl.Utf8), weight=pl.lit(0.0)
    )
    out = pl.concat([held, idle]).select([pl.col(c).cast(t) for c, t in DECISIONS.items()])
    return out.sort("open_time", "symbol", nulls_last=True)


def no_decisions() -> pl.DataFrame:
    return pl.DataFrame(schema=DECISIONS)


class LargeCoinBasket:
    def __init__(
        self,
        symbol: str = "UNIVERSE",
        top_n: int = 10,
        min_history_days: int = 180,
        min_volume_musd: int = 20,
    ) -> None:
        self.symbol = symbol
        self.top_n, self.min_history, self.min_volume = top_n, min_history_days, min_volume_musd
        self.name = f"top {top_n} coins by volume, equal weight ({min_history_days}d history)"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        if panel.is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        basket = weekly_basket(panel, self.top_n, self.min_history, self.min_volume)
        return as_decisions(panel, basket.with_columns(weight=pl.lit(1 / self.top_n)))
