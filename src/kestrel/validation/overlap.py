"""How much the pursued strategies overlap: are they separate edges or one bet?

Descriptive, from saved runs (no new settings, so no trials). For each strategy's daily
returns, over the days every strategy has:

- correlation with each other strategy, on all days and on BTC's bad days (BTC down more
  than ``bad_day`` that day), when diversification matters most;
- beta to BTC and the share of the daily variance BTC explains (R squared);
- mean daily return by month, grouped by BTC's month: falling (below -``flat``), flat
  (within) or rising (above);
- the Sharpe of an equal mix of all of them, and of the mix without each one: what each
  strategy adds to the rest.

Sharpe annualised with 365 days and a zero risk-free rate, as everywhere in Kestrel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

T = "close_time"
BTC = "BTC"


@dataclass(frozen=True)
class Exposure:
    beta: float
    r2: float
    """Share of the strategy's daily variance explained by BTC's."""


@dataclass(frozen=True)
class Overlap:
    names: list[str]
    days: int
    first: str
    last: str
    correlation: pl.DataFrame
    """All days; a ``name`` column and one column per strategy (BTC included)."""
    bad_days: int
    bad_correlation: pl.DataFrame
    """BTC's bad days only."""
    exposure: dict[str, Exposure]
    by_btc_month: pl.DataFrame
    """``btc_month`` (falling/flat/rising), ``months``, then each strategy's mean daily
    return in percent."""
    alone: dict[str, float]
    """Each strategy's own Sharpe over the common days (BTC included)."""
    mix_sharpe: float
    without: dict[str, float]
    """Sharpe of the equal mix without each strategy."""


def daily_returns(equity: pl.DataFrame, name: str) -> pl.DataFrame:
    """A run's equity curve (``close_time``, ``equity``) as daily returns."""
    return equity.sort(T).select(T, (pl.col("equity").pct_change()).alias(name)).drop_nulls()


def sharpe(r: pl.Series) -> float:
    sd = r.std()
    if sd is None or not isinstance(sd, float) or sd == 0:
        return 0.0
    return float(r.mean()) / sd * math.sqrt(365)  # type: ignore[arg-type]


def _correlations(frame: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
    return pl.DataFrame(
        {"name": cols} | {c: [frame.select(pl.corr(a, c)).item() for a in cols] for c in cols}
    )


def overlap(
    strategies: dict[str, pl.DataFrame],
    btc: pl.DataFrame,
    bad_day: float = 0.03,
    flat: float = 0.05,
) -> Overlap:
    """``strategies``: name -> daily returns (``close_time`` and a column of that name).
    ``btc``: BTC's daily returns, as a ``BTC`` column."""
    if not strategies:
        raise ValueError("no strategies")
    names = list(strategies)
    frame = btc.select(T, BTC)
    for name, r in strategies.items():
        frame = frame.join(r.select(T, name), on=T, how="inner")
    frame = frame.sort(T)
    if frame.height < 60:
        raise ValueError(f"only {frame.height} days in common")
    cols = [*names, BTC]

    bad = frame.filter(pl.col(BTC) < -bad_day)
    var_btc = frame[BTC].var()
    exposure = {}
    for n in names:
        cov = frame.select(pl.cov(n, BTC)).item()
        corr = frame.select(pl.corr(n, BTC)).item()
        exposure[n] = Exposure(beta=cov / var_btc, r2=corr * corr)

    monthly = (
        frame.group_by(pl.col(T).dt.truncate("1mo").alias("month"))
        .agg(
            ((pl.col(BTC) + 1).product() - 1).alias("_btc"),
            *[(pl.col(c).mean() * 100) for c in names],
        )
        .with_columns(
            btc_month=pl.when(pl.col("_btc") < -flat)
            .then(pl.lit("falling"))
            .when(pl.col("_btc") > flat)
            .then(pl.lit("rising"))
            .otherwise(pl.lit("flat"))
        )
    )
    order = {"falling": 0, "flat": 1, "rising": 2}
    by_month = (
        monthly.group_by("btc_month")
        .agg(pl.len().alias("months"), *[pl.col(c).mean() for c in names])
        .sort(pl.col("btc_month").replace_strict(order, return_dtype=pl.Int8))
    )

    def mix(members: list[str]) -> float:
        return sharpe(frame.select(pl.mean_horizontal(members)).to_series())

    return Overlap(
        names=names,
        days=frame.height,
        first=str(frame[T][0].date()),
        last=str(frame[T][-1].date()),
        correlation=_correlations(frame, cols),
        bad_days=bad.height,
        bad_correlation=_correlations(bad, cols),
        exposure=exposure,
        by_btc_month=by_month,
        alone={c: sharpe(frame[c]) for c in cols},
        mix_sharpe=mix(names),
        without={n: mix([m for m in names if m != n]) for n in names if len(names) > 1},
    )
