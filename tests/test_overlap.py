"""The overlap report: correlations, BTC exposure, BTC's months and what each strategy adds."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from kestrel.validation.overlap import daily_returns, overlap

T0 = datetime(2021, 1, 1, tzinfo=UTC)
N = 400


def frame(name: str, values: np.ndarray) -> pl.DataFrame:
    return pl.DataFrame(
        {"close_time": [T0 + timedelta(days=i) for i in range(len(values))], name: values}
    )


@pytest.fixture
def market() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    return rng.normal(0.001, 0.03, N), rng.normal(0.0005, 0.01, N)


def test_a_strategy_that_is_half_btc_has_beta_half_and_btc_explains_all_of_it(
    market: tuple[np.ndarray, np.ndarray],
) -> None:
    btc, noise = market
    o = overlap({"half": frame("half", btc / 2), "own": frame("own", noise)}, frame("BTC", btc))
    assert o.exposure["half"].beta == pytest.approx(0.5)
    assert o.exposure["half"].r2 == pytest.approx(1.0)
    assert o.exposure["own"].r2 < 0.05
    corr = dict(zip(o.correlation["name"], o.correlation["half"], strict=True))
    assert corr["BTC"] == pytest.approx(1.0)
    assert abs(corr["own"]) < 0.2
    assert o.days == N


def test_bad_days_are_btc_falls_beyond_the_threshold(market: tuple[np.ndarray, np.ndarray]) -> None:
    btc, noise = market
    o = overlap({"a": frame("a", noise), "b": frame("b", -noise)}, frame("BTC", btc))
    assert o.bad_days == int((btc < -0.03).sum())
    bad = dict(zip(o.bad_correlation["name"], o.bad_correlation["a"], strict=True))
    assert bad["b"] == pytest.approx(-1.0)


def test_months_are_grouped_by_btc_and_the_mix_is_compared_without_each(
    market: tuple[np.ndarray, np.ndarray],
) -> None:
    btc, noise = market
    o = overlap({"a": frame("a", noise), "b": frame("b", btc)}, frame("BTC", btc))
    assert set(o.by_btc_month["btc_month"]) <= {"falling", "flat", "rising"}
    assert o.by_btc_month["months"].sum() == 14  # 400 days from 2021-01-01
    assert set(o.without) == {"a", "b"}
    assert o.without["a"] == pytest.approx(o.alone["b"])  # the mix without a is b alone


def test_only_common_days_count_and_too_few_is_an_error(
    market: tuple[np.ndarray, np.ndarray],
) -> None:
    btc, noise = market
    late = frame("late", noise).filter(pl.col("close_time") >= T0 + timedelta(days=300))
    assert overlap({"late": late}, frame("BTC", btc)).days == N - 300
    with pytest.raises(ValueError, match="days in common"):
        overlap({"late": late.head(30)}, frame("BTC", btc))


def test_daily_returns_from_an_equity_curve() -> None:
    eq = pl.DataFrame(
        {"close_time": [T0 + timedelta(days=i) for i in range(3)], "equity": [100.0, 110.0, 99.0]}
    )
    assert daily_returns(eq, "s")["s"].to_list() == pytest.approx([0.1, -0.1])
