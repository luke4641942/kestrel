"""The forex trend rule (kestrel.strategies.fx_trend), on synthetic pairs only: real forex
history is not looked at before its note is frozen."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from kestrel.data.rates import Rates
from kestrel.fx_strategy import FX_STRATEGIES, make_fx_strategy, validated_fx_weights
from kestrel.strategies.fx_trend import TRADING_DAYS, FxTrend
from tests.backtest.test_fx_engine import pair_frame

T0 = datetime(2010, 1, 3, 22, tzinfo=UTC)


def walk(n: int, drift: float, seed: int, vol: float = 0.006) -> list[float]:
    rnd = random.Random(seed)
    price, out = 1.0, []
    for _ in range(n):
        price *= math.exp(rnd.gauss(drift, vol))
        out.append(price)
    return out


def days(n: int, skip: set[int] | None = None) -> list[datetime]:
    out = [T0 + timedelta(weeks=i // 5, days=i % 5) for i in range(n)]
    return [t for i, t in enumerate(out) if i not in (skip or set())]


def market(n: int = 400) -> dict[str, pl.DataFrame]:
    up, down = walk(n, 0.001, 1), walk(n, -0.001, 2)
    holiday = {150}  # GBPAUD has no candle on day 150
    gbp = [p for i, p in enumerate(down) if i not in holiday]
    return {
        "EURAUD": pair_frame(up, times=days(n)),
        "GBPAUD": pair_frame(gbp, times=days(n, holiday)),
    }


def test_long_the_riser_short_the_faller_sized_by_volatility() -> None:
    bars = market()
    s = FxTrend(lookback_days=100, vol_days=40, target_vol_pct=3)
    w = validated_fx_weights(s, bars, Rates({}), 3)
    last = w.row(-1, named=True)
    assert last["EURAUD"] > 0
    assert last["GBPAUD"] < 0
    eur = bars["EURAUD"]["close"]
    r = (eur / eur.shift(1)).log()
    vol = float(r.tail(40).std()) * math.sqrt(TRADING_DAYS)  # type: ignore[arg-type]
    assert last["EURAUD"] == pytest.approx(0.03 / vol)
    assert w.row(50, named=True)["EURAUD"] == 0  # no position before the lookback fills
    # GBPAUD's holiday: its weight stands from the day before.
    row = w.filter(pl.col("open_time") == days(400)[150]).row(0, named=True)
    before = w.filter(pl.col("open_time") == days(400)[149]).row(0, named=True)
    assert row["GBPAUD"] == before["GBPAUD"]


def test_the_cap_scales_every_weight_together() -> None:
    bars = market()
    loose = FxTrend(lookback_days=100, vol_days=40, target_vol_pct=20).target_weights(
        bars, Rates({})
    )
    capped = FxTrend(
        lookback_days=100, vol_days=40, target_vol_pct=20, max_gross_pct=100
    ).target_weights(bars, Rates({}))
    a, b = loose.row(-1, named=True), capped.row(-1, named=True)
    gross = abs(a["EURAUD"]) + abs(a["GBPAUD"])
    assert gross > 1
    assert abs(b["EURAUD"]) + abs(b["GBPAUD"]) == pytest.approx(1)
    assert b["EURAUD"] / b["GBPAUD"] == pytest.approx(a["EURAUD"] / a["GBPAUD"])


def test_registered_with_the_notes_defaults() -> None:
    spec = FX_STRATEGIES["fx:trend"]
    assert {p.name: p.default for p in spec.params} == {
        "lookback_days": 252,
        "vol_days": 60,
        "target_vol_pct": 3,
        "max_gross_pct": 300,
    }
    assert make_fx_strategy("fx:trend").name == "forex trend 252d, 3% vol per pair, cap 3x"
