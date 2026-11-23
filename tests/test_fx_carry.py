"""The forex carry rule (kestrel.strategies.fx_carry), on synthetic pairs and rates only:
real forex history is not looked at before its note is frozen."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from kestrel.data.rates import Rates
from kestrel.fx_strategy import FX_STRATEGIES, make_fx_strategy, validated_fx_weights
from kestrel.strategies.fx_carry import FxCarry
from tests.backtest.test_fx_engine import pair_frame
from tests.test_fx_trend import walk

T0 = datetime(2009, 11, 1, 22, tzinfo=UTC)  # a Sunday, 17:00 New York


def days(n: int) -> list[datetime]:
    return [T0 + timedelta(weeks=i // 5, days=i % 5) for i in range(n)]


def months(values: dict[str, list[float]]) -> Rates:
    """Monthly rates (%) from 2009-09, one list per currency."""
    out: dict[str, dict[date, float]] = {}
    for currency, vals in values.items():
        series: dict[date, float] = {}
        y, m = 2009, 9
        for v in vals:
            series[date(y, m, 1)] = v
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        out[currency] = series
    return Rates(out)


N = 160  # about seven and a half months of trading days
BARS = {
    "AUDUSD": pair_frame(walk(N, 0.0, 3), times=days(N)),
    "USDJPY": pair_frame(walk(N, 0.0, 4), times=days(N)),
    "EURUSD": pair_frame(walk(N, 0.0, 5), times=days(N)),
}
# AUD's rate drops from 5% to 2.5% in 2010-03: published by 2010-05 at the earliest.
RATES = months(
    {
        "AUD": [5.0] * 6 + [2.5] * 6,
        "USD": [2.0] * 12,
        "JPY": [0.0] * 12,
        "EUR": [2.5] * 12,
    }
)


def on(w: pl.DataFrame, day: datetime) -> dict[str, float]:
    return w.filter(pl.col("open_time") == day).row(0, named=True)


def first_in(month: int, year: int = 2010) -> datetime:
    return next(t for t in days(N) if (t - timedelta(hours=22)).month == month and t.year == year)


def test_towards_the_higher_yield_only_where_it_beats_the_markup() -> None:
    w = validated_fx_weights(FxCarry(vol_days=20), BARS, RATES, 3)
    march = on(w, first_in(3))
    assert march["AUDUSD"] > 0  # AUD 5% vs USD 2%: 3% beats 1%
    assert march["USDJPY"] > 0  # USD 2% vs JPY 0%
    assert march["EURUSD"] == 0  # EUR 2.5% vs USD 2%: 0.5% doesn't pay the markup
    eur = FxCarry(threshold_bps=0, vol_days=20).target_weights(BARS, RATES)
    assert on(eur, first_in(3))["EURUSD"] > 0


def test_a_rate_change_counts_only_once_published() -> None:
    w = FxCarry(vol_days=20).target_weights(BARS, RATES)
    # AUD falls to 2.5% in March (0.5% over USD): still held in April (known: February)...
    assert on(w, first_in(4))["AUDUSD"] > 0
    # ...and dropped in May, once March's average is out.
    assert on(w, first_in(5))["AUDUSD"] == 0


def test_weights_stand_for_the_month_sized_by_volatility() -> None:
    w = FxCarry(vol_days=20).target_weights(BARS, RATES)
    march = w.filter((pl.col("open_time") >= first_in(3)) & (pl.col("open_time") < first_in(4)))
    assert march["USDJPY"].n_unique() == 1
    f = BARS["USDJPY"].filter(pl.col("open_time") <= first_in(3))
    c = f["close"]
    vol = float((c / c.shift(1)).log().tail(20).std()) * math.sqrt(260)  # type: ignore[arg-type]
    assert march["USDJPY"][0] == pytest.approx(0.03 / vol)
    early = on(w, days(N)[0])
    assert early["AUDUSD"] == 0  # volatility window not full yet


def test_registered_with_the_notes_defaults() -> None:
    assert {p.name: p.default for p in FX_STRATEGIES["fx:carry"].params} == {
        "threshold_bps": 100,
        "vol_days": 60,
        "target_vol_pct": 3,
        "max_gross_pct": 300,
    }
    assert make_fx_strategy("fx:carry").name == "forex carry above 100bp, 3% vol per pair, cap 3x"
