"""Level retest and its control, on a hand-built path with swing_bars=2 and an ATR of 2.

    close: 100 -> 95 (bar 5) -> 110 (bar 20) -> 100 (30) -> 110 (40) -> 104 (46)
           -> 114 (56) -> 108 (62) -> 120 (74)

Swing highs 111 at bars 20 and 40 make a level at 111. Breakout needs a close above
111 + 0.5 x 2 = 112: bar 55 closes at 113. The retest needs a low within 0.25 x 2 of the
level and a close above it: bar 58 (low 111, close 112). Stop at the level minus one ATR:
109. These tests use synthetic bars only; the strategies must not be run on market data
until their research notes are frozen.
"""

from __future__ import annotations

import pytest

from kestrel.strategies.levels import LevelBreakout, LevelRetest
from kestrel.strategy import validated_brackets
from tests.chart import Path

TURNS = [
    (0, 100.0),
    (5, 95),
    (20, 110),
    (30, 100),
    (40, 110),
    (46, 104),
    (56, 114),
    (62, 108),
    (74, 120),
]


def entries(strategy: LevelRetest | LevelBreakout, path: Path) -> list[int]:
    return validated_brackets(strategy, path.frame())["enter"].arg_true().to_list()


def test_the_retest_of_a_broken_level_signals() -> None:
    b = validated_brackets(LevelRetest(swing_bars=2), Path(*TURNS).frame())
    assert b["enter"].arg_true().to_list() == [58]
    assert b["stop"][58] == 109
    assert b["target"][58] == pytest.approx(112 + 3 * 3)


def test_the_control_buys_the_breakout_close() -> None:
    b = validated_brackets(LevelBreakout(swing_bars=2), Path(*TURNS).frame())
    assert b["enter"].arg_true().to_list() == [55]
    assert b["stop"][55] == 109
    assert b["target"][55] == pytest.approx(113 + 3 * 4)


def test_one_swing_high_is_not_a_level() -> None:
    turns = TURNS.copy()
    turns[2] = (20, 106)  # high 107, more than half an ATR from 111
    assert entries(LevelRetest(swing_bars=2), Path(*turns)) == []
    assert entries(LevelBreakout(swing_bars=2), Path(*turns)) == []


def test_a_retest_must_come_soon() -> None:
    assert entries(LevelRetest(swing_bars=2, retest_bars=2), Path(*TURNS)) == []


def test_falling_back_through_the_level_cancels_the_setup() -> None:
    # Bar 58 dips to 105 and closes at 112: a retest.
    path = Path(*TURNS).bar(58, close=112, high=113, low=105)
    assert entries(LevelRetest(swing_bars=2), path) == [58]
    # Not if bar 57 first closes at 105, more than an ATR below the level.
    path.bar(57, close=105, high=114, low=104)
    assert entries(LevelRetest(swing_bars=2), path) == []


def test_the_control_ignores_retest_parameters() -> None:
    assert LevelBreakout(retest_bars=5, touch_atr_pct=0).retest is False


def test_a_stop_at_or_above_the_entry_is_never_signalled() -> None:
    enter, stop, target = [False], [None], [None]
    LevelRetest()._signal(0, 100.0, 100.0, enter, stop, target)  # type: ignore[arg-type]
    assert enter == [False]
