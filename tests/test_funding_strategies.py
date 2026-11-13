"""Funding crowding filters, on hand-built funding readings. Synthetic data only: the
strategies must not be run on market data until their research notes are frozen."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from kestrel.backtest.runs import RunRequest, execute
from kestrel.config import load_config
from kestrel.data import funding
from kestrel.data.binance_vision import Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategies.funding import FundingHold, FundingTrend
from kestrel.strategies.trend import TrendVolTarget
from kestrel.strategy import STRATEGIES, validated_weights
from tests.backtest.helpers import bars
from tests.data.test_funding import archive

REPO_CONFIG = Path(__file__).resolve().parents[1] / "kestrel.toml"
T0 = datetime(2024, 1, 1, tzinfo=UTC)


def with_funding(readings: list[float | None], closes: list[float] | None = None) -> pl.DataFrame:
    closes = closes or [100.0] * len(readings)
    frame = bars([closes[0], *closes[:-1]], closes=closes, step=timedelta(days=1))
    return frame.with_columns(funding_7d=pl.Series(readings, dtype=pl.Float64))


def test_hold_steps_aside_while_funding_is_crowded() -> None:
    w = validated_weights(FundingHold(), with_funding([0.1, 0.3, 0.5, 0.29, None]))
    assert w.to_list() == [1, 0, 0, 1, 1]  # unknown funding leaves the holding alone


def test_threshold_is_a_parameter() -> None:
    w = validated_weights(FundingHold(crowded_pct=50), with_funding([0.3, 0.5]))
    assert w.to_list() == [1, 0]


def rising(n: int) -> list[float]:
    return [100 * math.exp(0.01 * i) for i in range(n)]


def test_trend_keeps_its_own_weight_unless_crowded() -> None:
    n = 200
    readings: list[float | None] = [0.1] * n
    readings[150:155] = [0.4] * 5
    frame = with_funding(readings, rising(n))
    base = TrendVolTarget().target_weights(frame).to_list()
    w = validated_weights(FundingTrend(), frame).to_list()
    assert base[150] == 1.0
    assert w[:150] == base[:150]
    assert w[150:155] == [0.0] * 5
    assert w[155:] == base[155:]


@pytest.mark.parametrize("cls", [FundingHold, FundingTrend])
def test_it_needs_the_funding_feature(cls: type[FundingHold] | type[FundingTrend]) -> None:
    with pytest.raises(ValueError, match="needs the funding_7d feature"):
        cls().target_weights(bars([100.0] * 3))


def test_no_lookahead_on_random_funding() -> None:
    rnd = random.Random(3)
    n, level = 400, 0.1
    readings: list[float | None] = []
    closes, price = [], 100.0
    for _ in range(n):
        level = max(-0.2, level + rnd.gauss(0, 0.05))
        readings.append(None if rnd.random() < 0.02 else level)
        price *= math.exp(rnd.gauss(0.001, 0.03))
        closes.append(price)
    frame = with_funding(readings, closes)
    for strategy in (FundingHold(), FundingTrend()):
        w = validated_weights(strategy, frame)  # raises on lookahead
        assert 0 < w.sum() < n


def test_registered_as_research_needing_funding() -> None:
    for key in ("funding-hold", "funding-trend"):
        assert STRATEGIES[key].kind == "research"
        assert STRATEGIES[key].features == ("funding_7d",)


def test_a_backtest_attaches_the_coins_funding_itself(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    closes = [100.0] * 60
    store.write("ETHUSDT", Interval.D1, bars(closes, start=T0, step=timedelta(days=1)))
    # Calm funding, then 10 crowded days from day 30.
    rows = [
        (T0 - timedelta(days=8) + i * timedelta(hours=8), 8, 0.0006 if 114 <= i < 144 else 0.0)
        for i in range(3 * 68)
    ]
    h = funding.history_from("ETHUSDT", [funding.parse_archive(archive(rows))], [])
    funding.save(h, tmp_path)
    request = RunRequest(
        "funding-hold", "ETHUSDT", Interval.D1, Month(2024, 1), Month(2024, 2), Decimal(1000)
    )
    outcome = execute(request, store, load_config(REPO_CONFIG))
    sides = [f.fill.side.value for f in outcome.result.fills]
    assert sides[:3] == ["buy", "sell", "buy"]
    assert outcome.result.fills[0].time == T0 + timedelta(days=1)  # bought after the first close
