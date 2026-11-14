"""Turn of the month, on synthetic daily bars only: the strategy must not be run on market
data until its research note is frozen."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from kestrel.strategies.turn_of_month import TurnOfMonth, in_window
from kestrel.strategy import STRATEGIES, make_strategy, validated_weights
from tests.backtest.helpers import bars


def days(start: datetime, n: int):  # type: ignore[no-untyped-def]
    return bars([100.0] * n, start=start, step=timedelta(days=1))


def test_the_window() -> None:
    assert in_window(date(2024, 3, 1), 0, 1)
    assert not in_window(date(2024, 3, 2), 0, 1)
    assert not in_window(date(2024, 2, 29), 0, 1)
    assert in_window(date(2024, 2, 29), 1, 0)  # a leap February's last day
    assert in_window(date(2023, 2, 24), 5, 3)  # 28-day February: the 24th is 5th from last
    assert not in_window(date(2023, 2, 23), 5, 3)
    assert in_window(date(2023, 3, 3), 5, 3)
    assert not in_window(date(2023, 3, 4), 5, 3)


def test_the_base_rule_holds_only_the_first_day() -> None:
    # 2024-01-29 to 2024-02-03: the weight at Jan 31's close is for Feb 1.
    w = validated_weights(TurnOfMonth(), days(datetime(2024, 1, 29, tzinfo=UTC), 6)).to_list()
    assert w == [0.0, 0.0, 1.0, 0.0, 0.0, 0.0]


def test_the_wider_window() -> None:
    w = validated_weights(
        TurnOfMonth(last_days=5, first_days=3), days(datetime(2024, 1, 24, tzinfo=UTC), 14)
    ).to_list()
    # Decided Jan 26 for Jan 27 (5th from last) through Feb 2 for Feb 3.
    assert w == [0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0]


def test_bad_settings_and_bars_are_refused() -> None:
    with pytest.raises(ValueError, match="at least one day"):
        TurnOfMonth(last_days=0, first_days=0)
    hourly = bars([100.0] * 5, step=timedelta(hours=1))
    with pytest.raises(ValueError, match="daily bars"):
        TurnOfMonth().target_weights(hourly)


def test_it_is_registered_as_research() -> None:
    spec = STRATEGIES["turn-of-month"]
    assert spec.kind == "research"
    assert [p.name for p in spec.params] == ["last_days", "first_days"]
    made = make_strategy("turn-of-month", "BTCUSDT", {"last_days": 5, "first_days": 3})
    assert made.name == "turn of the month -5/+3 BTCUSDT"
