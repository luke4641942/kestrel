"""Monday range pullback, on the planted hourly week from tests/test_anchor_study.py.
Synthetic bars only: the strategy must not be run on market data until its research note
is frozen (and its raw-effect study has survived)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.engine import run_backtest
from kestrel.data.schema import Interval
from kestrel.strategies.weekday import MondayMidPullback
from kestrel.strategy import STRATEGIES, make_strategy, validated_brackets
from tests.backtest.helpers import bars, config
from tests.test_anchor_study import HOUR, MONDAY_AEST, _line, hourly, planted, random_walk_hours

TUESDAY_01 = 24 + 1  # bar index of Tuesday 01:00 AEST in the first week
FRIDAY_23 = 24 * 4 + 23


def signals(frame: pl.DataFrame, **params: int) -> pl.DataFrame:
    return validated_brackets(MondayMidPullback(**params), frame)


def test_the_pullback_after_a_bullish_break_signals_with_the_monday_low_as_stop() -> None:
    b = signals(planted(1))
    assert b["enter"].arg_true().to_list() == [TUESDAY_01]
    assert b["stop"][TUESDAY_01] == pytest.approx(99.55)  # Monday's low
    assert b["target"][TUESDAY_01] is None
    exits = b["exit"].arg_true().to_list()
    assert exits[0] == FRIDAY_23  # Friday's last bar, then every bar of the weekend
    assert exits == list(range(FRIDAY_23, 168))


def test_the_trade_by_hand() -> None:
    # Buy Tuesday 02:00's open (100.6); sell Saturday 00:00's open (Friday's last close).
    frame = planted(1)
    result = run_backtest(
        MondayMidPullback(), frame, Interval.H1, config(costs=False), Decimal(1000)
    )
    (t,) = result.trades
    friday_close = _line(100.2, 106.0, 108)[83]
    assert t.exit_reason == "signal"
    assert float(t.entry_price) == pytest.approx(100.6)
    assert float(t.exit_price) == pytest.approx(friday_close)
    assert t.exit_time == MONDAY_AEST + 5 * 24 * HOUR  # Saturday 00:00 AEST
    assert float(t.r_multiple) == pytest.approx((friday_close - 100.6) / (100.6 - 99.55))


def test_one_trade_a_week() -> None:
    b = signals(planted(4))
    assert b["enter"].sum() == 4


def test_a_bearish_week_has_no_trade() -> None:
    week = [99.6 if h % 2 == 0 else 100.4 for h in range(24)]  # Monday: 99.55 to 100.45
    week += _line(100.4, 99.0, 6) + _line(99.0, 100.1, 138)  # breaks the low, then recovers
    assert signals(hourly(week))["enter"].sum() == 0


def test_a_week_with_a_missing_monday_bar_is_skipped() -> None:
    frame = planted(2).filter(pl.col("open_time") != MONDAY_AEST + 5 * HOUR)
    entries = signals(frame)["enter"].sum()
    assert entries == 1  # only the second week trades


def test_no_entry_on_the_exit_bar() -> None:
    # With Tuesday as the exit day, Tuesday 01:00's pullback still comes before Tuesday's
    # last bar, so it trades; Tuesday 23:00 is the exit.
    b = signals(planted(1), exit_day=2)
    assert b["enter"].arg_true().to_list() == [TUESDAY_01]
    assert b["exit"].arg_true().to_list()[0] == 24 + 23


def test_bars_longer_than_an_hour_are_refused() -> None:
    with pytest.raises(ValueError, match="1h or shorter"):
        MondayMidPullback().brackets(bars([100.0] * 30, step=timedelta(hours=4)))


def test_exit_day_must_be_after_monday() -> None:
    with pytest.raises(ValueError, match="exit_day"):
        MondayMidPullback(exit_day=1)


def test_too_little_data_signals_nothing() -> None:
    assert signals(planted(1).head(1))["enter"].sum() == 0


def test_no_lookahead_on_a_random_walk() -> None:
    b = validated_brackets(make_strategy("monday-pullback", "BTCUSDT"), random_walk_hours(40))  # type: ignore[arg-type]
    assert 0 < b["enter"].sum() <= 40  # at most one a week


def test_registered_as_research() -> None:
    spec = STRATEGIES["monday-pullback"]
    assert spec.kind == "research"
    assert [p.name for p in spec.params] == ["tolerance_bps", "exit_day"]
