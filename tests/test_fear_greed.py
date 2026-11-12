"""The Fear & Greed contrarian strategy, on hand-built readings. Synthetic data only: the
strategy must not be run on market data until its research note is frozen."""

from __future__ import annotations

import math
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from kestrel.backtest.runs import RunRequest, execute
from kestrel.config import load_config
from kestrel.data import sentiment
from kestrel.data.binance_vision import Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategies.sentiment import FearGreedContrarian
from kestrel.strategy import STRATEGIES, validated_weights
from tests.backtest.helpers import bars
from tests.data.test_sentiment import payload

REPO_CONFIG = Path(__file__).resolve().parents[1] / "kestrel.toml"


def with_readings(readings: list[int | None]) -> pl.DataFrame:
    frame = bars([100.0] * len(readings), step=timedelta(days=1))
    return frame.with_columns(fear_greed=pl.Series(readings, dtype=pl.Int16))


def weights(readings: list[int | None], **params: int) -> list[float]:
    return validated_weights(FearGreedContrarian(**params), with_readings(readings)).to_list()


def test_buys_in_extreme_fear_and_sells_in_extreme_greed() -> None:
    #           neutral  fear(25)  greed but not extreme  extreme greed  neutral
    assert weights([50, 25, 60, 75, 76, 50]) == [0, 1, 1, 1, 0, 0]


def test_holds_through_the_middle_and_ignores_unknown_readings() -> None:
    assert weights([20, None, 40, None, 80, None, 10]) == [1, 1, 1, 1, 0, 0, 1]
    assert weights([None, None, 30]) == [0, 0, 0]


def test_thresholds_are_parameters() -> None:
    assert weights([30, 70], fear_max=30, greed_min=70) == [1, 0]
    with pytest.raises(ValueError, match="fear_max must be below greed_min"):
        FearGreedContrarian(fear_max=60, greed_min=60)


def test_it_needs_the_sentiment_feature() -> None:
    with pytest.raises(ValueError, match="needs the fear_greed feature"):
        FearGreedContrarian().target_weights(bars([100.0] * 3))


def test_no_lookahead_on_a_random_walk_of_readings() -> None:
    rnd = random.Random(2)
    readings: list[int | None] = []
    level = 50.0
    for _ in range(600):
        level = min(100, max(0, level + rnd.gauss(0, 6)))
        readings.append(None if rnd.random() < 0.02 else round(level))
    w = validated_weights(FearGreedContrarian(), with_readings(readings))  # raises on lookahead
    assert 0 < w.sum() < len(readings)


def test_registered_as_research_needing_sentiment() -> None:
    spec = STRATEGIES["fear-greed"]
    assert spec.kind == "research"
    assert spec.features == ("fear_greed",)


def test_a_backtest_attaches_the_index_itself(tmp_path: Path) -> None:
    start = date(2024, 1, 1)
    rnd = random.Random(5)
    price, closes = 100.0, []
    for _ in range(120):
        price *= math.exp(rnd.gauss(0, 0.03))
        closes.append(price)
    store = ParquetStore(tmp_path)
    store.write(
        "BTCUSDT",
        Interval.D1,
        bars(
            [closes[0], *closes[:-1]],
            closes=closes,
            start=datetime(2024, 1, 1, tzinfo=UTC),
            step=timedelta(days=1),
        ),
    )
    readings = {
        start + timedelta(days=i): (15 if i % 40 < 10 else 85 if i % 40 < 20 else 50)
        for i in range(120)
    }
    sentiment.save(sentiment.parse(payload(readings)), tmp_path)
    request = RunRequest(
        "fear-greed",
        "BTCUSDT",
        Interval.D1,
        Month.parse("2024-01"),
        Month.parse("2024-04"),
        Decimal(1000),
    )
    outcome = execute(request, store, load_config(REPO_CONFIG))
    assert [f.fill.side.value for f in outcome.result.fills][:2] == [
        "buy",
        "sell",
    ]  # fear, then greed
    assert outcome.result.fills[0].time == datetime(
        2024, 1, 2, tzinfo=UTC
    )  # day 1's reading, next open
