"""Gaps in one coin's bars (findings 31): a day or more missing splits its history, a
one-coin run refuses to cross one, and an exchange outage of a few hours doesn't count."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.engine import run_backtest
from kestrel.backtest.vectorised import run_vectorised
from kestrel.data.panel import gaps, refuse_gaps, unbroken
from kestrel.data.schema import Interval
from kestrel.strategy import BuyAndHold
from tests.backtest.helpers import bars, config

START = datetime(2022, 1, 1, tzinfo=UTC)
DAY, FOUR_HOURS = timedelta(days=1), timedelta(hours=4)


def daily(n: int) -> pl.DataFrame:
    return bars([100.0 + i for i in range(n)], start=START, step=DAY)


def test_a_missing_day_splits_a_coins_history() -> None:
    frame = daily(30)
    broken = pl.concat([frame.head(10), frame.slice(13)])  # 3 days missing
    assert gaps(frame, Interval.D1) == []
    assert unbroken(frame, Interval.D1) == [(START, START + 29 * DAY)]
    assert gaps(broken, Interval.D1) == [(START + 9 * DAY, START + 13 * DAY)]
    assert unbroken(broken, Interval.D1) == [
        (START, START + 9 * DAY),
        (START + 13 * DAY, START + 29 * DAY),
    ]
    assert unbroken(frame.head(0), Interval.D1) == []


def test_an_outage_of_a_few_hours_is_not_a_gap() -> None:
    frame = bars([100.0 + i for i in range(40)], start=START, step=FOUR_HOURS)
    outage = pl.concat([frame.head(10), frame.slice(12)])  # two 4h bars, 8 hours
    long = pl.concat([frame.head(10), frame.slice(16)])  # six bars, a whole day
    assert gaps(outage, Interval.H4) == []
    assert len(gaps(long, Interval.H4)) == 1


def test_a_one_coin_run_refuses_to_cross_a_gap_and_names_the_histories() -> None:
    broken = pl.concat([daily(30).head(10), daily(30).slice(13)])
    with pytest.raises(ValueError, match="2022-01-01 to 2022-01-10; 2022-01-14 to 2022-01-30"):
        refuse_gaps(broken, Interval.D1, "XUSDT")
    refuse_gaps(daily(30), Interval.D1, "XUSDT")  # no gap: nothing raised
    cfg = config(costs=False)
    for engine in (run_backtest, run_vectorised):
        with pytest.raises(ValueError, match="nothing is computed across a gap"):
            engine(BuyAndHold("BTCUSDT"), broken, Interval.D1, cfg, Decimal(1000))
        engine(BuyAndHold("BTCUSDT"), broken.slice(10), Interval.D1, cfg, Decimal(1000))
