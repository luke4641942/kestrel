"""Trend following with volatility targeting, on hand-built daily closes. Synthetic bars
only: the strategy must not be run on market data until its research note is frozen."""

from __future__ import annotations

import math
from datetime import timedelta
from statistics import stdev

import pytest

from kestrel.strategies.trend import TrendVolTarget
from kestrel.strategy import STRATEGIES, make_strategy, validated_weights
from tests.backtest.helpers import bars
from tests.chart import random_walk

DAY = timedelta(days=1)


def daily(log_returns: list[float], step: timedelta = DAY):  # type: ignore[no-untyped-def]
    closes, price = [100.0], 100.0
    for r in log_returns:
        price *= math.exp(r)
        closes.append(price)
    return bars([closes[0], *closes[:-1]], closes=closes, step=step)


def weights(frame, **params: int) -> list[float]:  # type: ignore[no-untyped-def]
    return validated_weights(TrendVolTarget(**params), frame).to_list()


def test_cash_until_the_longest_lookback_has_filled() -> None:
    w = weights(daily([0.01] * 200))
    assert w[:120] == [0.0] * 120  # bar 120 is the first with a close 120 days earlier
    assert w[120] == 1.0


def test_a_smooth_rise_is_fully_in_and_a_fall_is_out() -> None:
    # Constant returns have zero volatility, so the size cap of 1 applies.
    assert weights(daily([0.01] * 200))[-1] == 1.0
    assert weights(daily([-0.01] * 200))[-1] == 0.0


def test_partial_agreement_gives_a_partial_position() -> None:
    # 150 days up, then 30 days down: the 20-day trend is down, the 60 and 120-day are up.
    w = weights(daily([0.01] * 150 + [-0.002] * 30))
    assert w[-1] == pytest.approx(2 / 3)


def test_size_falls_when_volatility_is_above_target() -> None:
    returns = [0.05, -0.03] * 100  # rising on average, with large daily swings
    w = weights(daily(returns))
    vol = stdev(returns[-30:]) * math.sqrt(365)  # about 78% a year
    assert w[-1] == pytest.approx(0.5 / vol)
    assert weights(daily(returns), target_vol_pct=150)[-1] == 1.0  # never more than fully in


def test_lookbacks_are_days_whatever_the_bar_size() -> None:
    # On 4h bars, 120 days is 720 bars.
    w = weights(daily([0.002] * 800, step=timedelta(hours=4)))
    assert w[719] == 0.0
    assert w[720] == 1.0


def test_lookbacks_must_increase() -> None:
    with pytest.raises(ValueError, match="must increase"):
        TrendVolTarget(fast_days=60, mid_days=60)


def test_too_little_data_is_cash() -> None:
    assert weights(daily([])) == [0.0]


def test_no_lookahead_on_a_random_walk() -> None:
    w = validated_weights(make_strategy("trend-vol-target", "BTCUSDT"), random_walk())  # type: ignore[arg-type]
    assert 0 < w.max() <= 1  # it trades, and never above fully invested


def test_registered_as_research() -> None:
    spec = STRATEGIES["trend-vol-target"]
    assert spec.kind == "research"
    assert [p.name for p in spec.params] == [
        "fast_days",
        "mid_days",
        "slow_days",
        "vol_days",
        "target_vol_pct",
    ]
