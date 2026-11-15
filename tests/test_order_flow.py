"""Order flow Model 2 as a bracket strategy: entries, levels, the daily exit, no lookahead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from kestrel.strategies.order_flow import OrderFlow, signals, value_area
from kestrel.strategy import make_strategy, validated_brackets
from tests.test_cost_gate import DAY, previous_day, session


def a_failed_breakdown(start: datetime = DAY, pad: int = 0) -> pl.DataFrame:
    """The previous day, then a day that breaks below its value area and reclaims it."""
    before = previous_day()
    area = value_area(before)
    assert area is not None
    val = area[0]
    lows = [100.0] * pad + [100.0, val - 0.3, val - 0.5, val - 0.1, val - 0.05, 100.0, 100.0]
    closes = [100.0] * pad + [100.0, val - 0.2, val - 0.3, val - 0.02, val + 0.02, 100.0, 100.0]
    return pl.concat([before, session(lows, closes, start=start)])


def test_the_signal_bar_enters_with_the_breakdown_low_and_the_point_of_control() -> None:
    bars = a_failed_breakdown()
    (sig,) = signals(bars)
    frame = OrderFlow("ETHUSDT").brackets(bars)
    rows = frame.with_row_index().filter(pl.col("enter"))
    assert rows.height == 1
    i = rows["index"][0]
    assert bars["open_time"][i] == sig.at == DAY + timedelta(minutes=4)
    assert rows["stop"][0] == sig.stop
    assert rows["target"][0] == sig.target
    assert sig.stop < sig.entry < sig.target


def test_every_days_last_bar_exits_and_a_signal_there_is_skipped() -> None:
    midnight = session([1.0] * 4, [1.0] * 4, start=DAY - timedelta(minutes=2))
    frame = OrderFlow().brackets(midnight)  # bars at 23:58, 23:59, 00:00, 00:01
    assert midnight.filter(frame["exit"])["open_time"].to_list() == [DAY - timedelta(minutes=1)]
    # A day whose reclaim lands on 23:59 has no trade: its entry would be the next day.
    late = a_failed_breakdown(start=DAY + timedelta(hours=23, minutes=55))
    (sig,) = signals(late)
    assert sig.at == DAY + timedelta(hours=23, minutes=59)
    assert not OrderFlow().brackets(late)["enter"].any()


def test_no_setup_when_the_previous_day_is_missing() -> None:
    bars = a_failed_breakdown(start=DAY + timedelta(days=1))  # a day with no bars between
    assert signals(bars) == []


def test_no_lookahead_and_the_registry_builds_it() -> None:
    bars = a_failed_breakdown(pad=20)
    strategy = make_strategy("order-flow", "SOLUSDT", {"reclaim_bars": 10})
    assert isinstance(strategy, OrderFlow)
    assert strategy.reclaim_bars == 10
    frame = validated_brackets(strategy, bars)  # raises if a truncated history disagrees
    assert frame["enter"].sum() == 1


def test_settings_change_the_profile_and_the_reclaim_window() -> None:
    bars = a_failed_breakdown()
    assert OrderFlow(reclaim_bars=2).brackets(bars)["enter"].sum() == 0  # reclaim is 4th bar
    assert OrderFlow(reclaim_bars=15).brackets(bars)["enter"].sum() == 1
    wide = value_area(previous_day(), bin_bps=50)
    narrow = value_area(previous_day(), bin_bps=5)
    assert wide is not None
    assert narrow is not None
    assert wide != narrow
    assert datetime(2024, 3, 1, tzinfo=UTC) == DAY
