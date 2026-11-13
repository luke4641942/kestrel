"""Volatility-managed holding and one-week momentum, on hand-built daily closes (2024-01-01
is a Monday). Synthetic bars only: the strategies must not be run on market data until
their research notes are frozen."""

from __future__ import annotations

import math
import random
from datetime import timedelta
from statistics import stdev

import pytest

from kestrel.strategies.trend import TrendVolTarget
from kestrel.strategies.volhold import VolManagedHold
from kestrel.strategies.weekly import WeeklyMomentum
from kestrel.strategy import STRATEGIES, make_strategy, validated_weights
from tests.backtest.helpers import bars

DAY = timedelta(days=1)


def daily(log_returns: list[float], step: timedelta = DAY):  # type: ignore[no-untyped-def]
    closes, price = [100.0], 100.0
    for r in log_returns:
        price *= math.exp(r)
        closes.append(price)
    return bars([closes[0], *closes[:-1]], closes=closes, step=step)


# ---------- volatility-managed holding ----------


def vol_weights(frame, **params: int) -> list[float]:  # type: ignore[no-untyped-def]
    return validated_weights(VolManagedHold(**params), frame).to_list()


def test_fully_in_when_calm_and_scaled_down_when_wild() -> None:
    returns = [0.05, -0.03] * 40
    w = vol_weights(daily(returns))
    assert w[:30] == [0.0] * 30  # cash until 30 returns exist
    vol = stdev(returns[-30:]) * math.sqrt(365)
    assert w[-1] == pytest.approx(0.5 / vol)
    assert vol_weights(daily([0.001] * 40))[-1] == 1.0  # zero volatility: capped at 1


def test_it_is_trend_vol_target_with_every_trend_vote_up() -> None:
    # On a rising path every trend vote is 1, so both size identically.
    returns = [0.03 if i % 2 else -0.02 for i in range(300)]
    frame = daily(returns)
    held = vol_weights(frame)
    trend = TrendVolTarget().target_weights(frame).to_list()
    assert held[150:] == pytest.approx(trend[150:])


def test_volatility_days_scale_with_bar_size() -> None:
    w = vol_weights(daily([0.002, -0.001] * 120, step=timedelta(hours=4)))
    assert w[179] == 0.0  # 30 days of 4h bars is 180 returns
    assert w[180] > 0
    assert vol_weights(daily([])) == [0.0]


# ---------- one-week momentum ----------


def weekly(frame) -> list[float]:  # type: ignore[no-untyped-def]
    return validated_weights(WeeklyMomentum(), frame).to_list()


def test_holds_the_week_after_a_rising_week() -> None:
    # Day 6 (7 Jan) is the first Sunday, but has no close 7 days earlier: cash.
    up = weekly(daily([0.01] * 20))
    assert up[:13] == [0.0] * 13
    assert up[13:20] == [1.0] * 7  # decided at day 13's close (14 Jan), held through day 19
    down = weekly(daily([-0.01] * 20))
    assert sum(down) == 0


def test_a_week_is_seven_days_by_the_clock() -> None:
    frame = daily([0.01] * 6 + [-0.2] + [0.01] * 13)  # day 7 drops 20%
    w = weekly(frame)
    # Sunday day 13: close vs day 6's close spans the drop: down, so cash.
    assert w[13] == 0.0
    # A missing bar: the week's start is looked up by date, and a missing start is no signal.
    gappy = daily([0.01] * 20).filter(frame["open_time"] != frame["open_time"][6])
    assert weekly(gappy)[12] == 0.0  # Sunday day 13 has no close on day 6


def test_weekly_needs_daily_bars() -> None:
    with pytest.raises(ValueError, match="daily bars"):
        WeeklyMomentum().target_weights(daily([0.01] * 20, step=timedelta(hours=4)))
    assert weekly(daily([])) == [0.0]


@pytest.mark.parametrize("key", ["vol-hold", "weekly-momentum"])
def test_no_lookahead_on_a_random_walk(key: str) -> None:
    rnd = random.Random(4)
    frame = daily([rnd.gauss(0.0005, 0.03) for _ in range(800)])
    w = validated_weights(make_strategy(key, "BTCUSDT"), frame)  # type: ignore[arg-type]
    assert 0 < w.sum() < len(w)


def test_registered_as_research() -> None:
    for key in ("vol-hold", "weekly-momentum"):
        assert (STRATEGIES[key].kind, STRATEGIES[key].universe) == ("research", False)
