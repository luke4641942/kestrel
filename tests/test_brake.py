"""The crash brake: a close-based trailing stop over a strategy's weights, re-armed at the
strategy's next decision. Synthetic bars only: research notes decide when it meets data."""

from __future__ import annotations

import random

import pytest

from kestrel.indicators import atr
from kestrel.strategies.brake import TrendVolTargetBrake, WeeklyMomentumBrake, brake
from kestrel.strategies.trend import TrendVolTarget
from kestrel.strategies.weekly import WeeklyMomentum
from kestrel.strategy import STRATEGIES, make_strategy, validated_weights
from tests.test_volhold_weekly import daily

CLOSE = [100.0, 105, 110, 100, 95, 96, 97]
NO_DECISION = [True] + [False] * 6


def test_trips_below_the_high_less_the_multiple_and_waits() -> None:
    # High 110 at t2, ATR 5, multiple 2: the level is 100. A close of 100 holds (not below),
    # 95 trips, and the brake stays on with no new decision.
    assert brake(CLOSE, [5.0] * 7, [1.0] * 7, NO_DECISION, 2) == [1, 1, 1, 1, 0, 0, 0]


def test_the_next_decision_re_arms_it_from_a_fresh_high() -> None:
    decisions = [True, False, False, False, False, True, False]
    # Re-armed at t5: the high starts again at 96 (level 86), so it holds.
    assert brake(CLOSE, [5.0] * 7, [1.0] * 7, decisions, 2) == [1, 1, 1, 1, 0, 1, 1]


def test_it_only_ever_lowers_a_target() -> None:
    weights = [0.4, 0.4, 0.0, 0.7, 0.7, 0.7, 0.7]
    out = brake(CLOSE, [5.0] * 7, weights, NO_DECISION, 2)
    assert all(o in (w, 0.0) for o, w in zip(out, weights, strict=True))
    # Flat at t2 forgets the old high: re-entry at t3 measures from 100, so 95 at t4 holds.
    assert out == [0.4, 0.4, 0.0, 0.7, 0.7, 0.7, 0.7]
    assert brake(CLOSE, [None] * 7, [1.0] * 7, NO_DECISION, 2) == [1.0] * 7  # ATR warming up
    assert brake(CLOSE, [5.0] * 7, [1.0] * 7, NO_DECISION, 4) == [1.0] * 7  # a wider brake


def test_weekly_brake_sells_inside_a_crash_week_and_returns_on_sunday() -> None:
    # 2024-01-01 is a Monday. Steady rises, then day 23 (Wednesday 24 Jan) falls 25%.
    returns = [0.01] * 22 + [-0.25] + [0.01] * 20
    frame = daily(returns)
    base = validated_weights(WeeklyMomentum("ETHUSDT"), frame).to_list()
    braked = validated_weights(WeeklyMomentumBrake("ETHUSDT"), frame).to_list()
    assert base[23] == 1.0  # the plain rule sits through the fall
    assert braked[23] == 0.0  # the brake sells at that close
    assert braked[24:27] == [0.0] * 3  # and waits for Sunday 28 Jan (day 27)
    # On Sunday the plain rule decides again: the week fell, so both are in cash.
    assert braked[27] == base[27] == 0.0
    assert braked[:23] == base[:23]  # before the crash, identical


def test_trend_brake_matches_the_plain_rule_without_a_crash() -> None:
    returns = [0.004 if i % 3 else -0.002 for i in range(400)]
    frame = daily(returns)
    plain = TrendVolTarget().target_weights(frame).to_list()
    braked = TrendVolTargetBrake().target_weights(frame).to_list()
    assert braked == pytest.approx(plain)


def test_trend_brake_waits_for_the_votes_to_change() -> None:
    returns = [0.004] * 200 + [-0.12] + [0.004] * 30
    frame = daily(returns)
    trend = TrendVolTargetBrake()
    braked = trend.target_weights(frame).to_list()
    votes = trend.vote_count(frame).to_list()
    plain = TrendVolTarget().target_weights(frame).to_list()
    crash = 201  # close i comes from return i - 1
    assert plain[crash] > 0
    assert braked[crash] == 0.0
    back = next(i for i in range(crash + 1, len(braked)) if braked[i] > 0)
    assert votes[back] != votes[back - 1]  # it came back on a change in the votes
    assert all(votes[i] == votes[crash] for i in range(crash + 1, back))  # not before


@pytest.mark.parametrize("key", ["weekly-momentum-brake", "trend-vol-target-brake"])
def test_no_lookahead_on_a_random_walk(key: str) -> None:
    rnd = random.Random(11)
    frame = daily([rnd.gauss(0.001, 0.04) for _ in range(800)])
    w = validated_weights(make_strategy(key, "ETHUSDT"), frame)  # type: ignore[arg-type]
    assert 0 < w.sum() < len(w)
    assert atr(frame, 14).null_count() == 13


def test_registered_as_research_with_one_setting() -> None:
    for key in ("weekly-momentum-brake", "trend-vol-target-brake"):
        spec = STRATEGIES[key]
        assert spec.kind == "research"
        assert next(p for p in spec.params if p.name == "brake_atr").default == 2
    with pytest.raises(ValueError, match="at least 1"):
        WeeklyMomentumBrake(brake_atr=0)
    assert WeeklyMomentumBrake().target_weights(daily([])).to_list() == [0.0]
    assert TrendVolTargetBrake().target_weights(daily([0.01] * 5).clear()).is_empty()
