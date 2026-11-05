"""Trendline strategies on hand-built paths with swing_bars=2 and an ATR of 2 (see
tests/chart.py). These use synthetic bars only; the strategies must not be run on market
data until their research notes are frozen.
"""

from __future__ import annotations

import pytest

from kestrel.strategies.trendlines import (
    SwingPullback,
    TrendlineBounce,
    TrendlineBreakout,
    WedgeBreakout,
)
from kestrel.strategy import STRATEGIES, make_strategy, strategy_source, validated_brackets
from tests.chart import Path, random_walk

Strat = TrendlineBounce | SwingPullback | TrendlineBreakout | WedgeBreakout


def entries(strategy: Strat, path: Path, n: int | None = None) -> list[int]:
    return validated_brackets(strategy, path.frame(n))["enter"].arg_true().to_list()


# Uptrend: swing lows 89 (bar 10) and 99 (bar 40), swing highs 111 (bar 30) and 121
# (bar 60). The line through the lows rises 1/3 a bar: 109 at bar 70.
UPTREND = [(0, 100.0), (10, 90), (30, 110), (40, 100), (60, 120), (78, 102), (100, 124)]


def test_bounce_off_the_rising_line() -> None:
    b = validated_brackets(TrendlineBounce(swing_bars=2), Path(*UPTREND).frame())
    # Bar 70: low 109 touches the line (109), close 110 above it. Bar 73 closes at 107,
    # more than an ATR below the line (110), which spends it; the next line, through the
    # swing low at bar 78, is never touched again.
    assert b["enter"].arg_true().to_list() == [70]
    assert b["stop"][70] == 107  # line minus one ATR
    assert b["target"][70] == pytest.approx(110 + 3 * 3)


def test_pullback_control_buys_near_the_last_swing_low() -> None:
    b = validated_brackets(SwingPullback(swing_bars=2), Path(*UPTREND).frame())
    # Lows within 1.5 ATR (3) of the swing low at 99 from bar 77; from bar 80 the newer
    # swing low at 101 (bar 78) takes over. The engine only acts on the first while flat.
    assert b["enter"].arg_true().to_list() == [77, 78, 79, 80, 81]
    assert b["stop"][77] == 97
    assert b["target"][77] == pytest.approx(103 + 3 * 6)
    assert b["stop"][80] == 99


def test_no_uptrend_without_a_higher_high() -> None:
    path = Path((0, 100.0), (10, 90), (30, 110), (40, 100), (50, 108), (68, 90))
    assert entries(TrendlineBounce(swing_bars=2), path) == []
    assert entries(SwingPullback(swing_bars=2), path) == []


def test_a_broken_line_is_not_bought_again() -> None:
    # Bar 67 touches the line (108) and closes above it.
    touch = Path(*UPTREND).bar(67, close=110, high=114, low=104)
    assert entries(TrendlineBounce(swing_bars=2), touch, 68) == [67]
    # The same touch after bar 66 closed more than an ATR below the line: not a bounce,
    # even though bar 66's swing low isn't confirmed yet.
    broken = touch.bar(66, close=104, high=115, low=103)
    assert entries(TrendlineBounce(swing_bars=2), broken, 68) == []


# Falling line through swing highs 121 (bar 20) and 116 (bar 45): -0.2 a bar.
FALLING = [(0, 100.0), (20, 120), (35, 105), (45, 115), (54, 106), (80, 132)]


def test_breakout_through_a_falling_line() -> None:
    b = validated_brackets(TrendlineBreakout(swing_bars=2), Path(*FALLING).frame())
    # Bar 62 closes at 114, above the line (112.6) plus half an ATR. Bar 63 is above it
    # too, but each line is used once.
    assert b["enter"].arg_true().to_list() == [62]
    assert b["stop"][62] == 105  # lowest low since the second swing high
    assert b["target"][62] == pytest.approx(114 + 3 * 9)


def test_rising_swing_highs_make_no_falling_line() -> None:
    path = Path((0, 100.0), (20, 120), (35, 105), (55, 125), (70, 110), (90, 130))
    assert entries(TrendlineBreakout(swing_bars=2), path) == []


# Upper line through 131 (bar 30) and 116 (bar 75); lower through 99 (bar 60) and 96
# (bar 93). The gap narrows from 29.3 at bar 30 to 14 at bar 93: 48%, under 60%.
WEDGE = [(0, 100.0), (30, 130), (60, 100), (75, 115), (93, 97), (120, 124)]


def test_breakout_from_a_falling_wedge() -> None:
    b = validated_brackets(WedgeBreakout(swing_bars=2), Path(*WEDGE).frame())
    # Bar 104 closes at 108, above the upper line (106.3) plus half an ATR.
    assert b["enter"].arg_true().to_list() == [104]
    assert b["stop"][104] == 96  # the latest swing low
    assert b["target"][104] == pytest.approx(108 + 3 * 12)


def test_lines_that_do_not_converge_are_not_a_wedge() -> None:
    # Lower line through 99 and 85: the gap only narrows to 95%. A plain falling-line
    # breakout still fires there, so the wedge is the stricter pattern.
    path = Path((0, 100.0), (30, 130), (60, 100), (75, 115), (104, 86), (140, 122))
    assert entries(WedgeBreakout(swing_bars=2), path) == []
    assert entries(TrendlineBreakout(swing_bars=2), path) != []


def test_a_wedge_that_breaks_down_is_spent() -> None:
    spike = Path(*WEDGE).bar(97, close=112, high=113, low=95)
    assert entries(WedgeBreakout(swing_bars=2), spike, 98) == [97]
    # Bar 96 closes below the lower line (95.7) first: no breakout from that wedge.
    crashed = spike.bar(96, close=95, high=99, low=94.5)
    assert entries(WedgeBreakout(swing_bars=2), crashed, 98) == []


KEYS = ["level-retest", "level-breakout", "trendline-bounce", "swing-pullback"]
KEYS += ["trendline-breakout", "wedge-breakout"]


@pytest.mark.parametrize("key", KEYS)
def test_no_lookahead_on_a_random_walk(key: str) -> None:
    b = validated_brackets(make_strategy(key, "BTCUSDT"), random_walk())  # type: ignore[arg-type]
    assert b["enter"].sum() > 0  # it finds setups in noise; whether they pay is the note's test


def test_registered_with_controls_marked() -> None:
    kinds = {k: STRATEGIES[k].kind for k in KEYS}
    assert kinds == {
        "level-retest": "research",
        "level-breakout": "control",
        "trendline-bounce": "research",
        "swing-pullback": "control",
        "trendline-breakout": "research",
        "wedge-breakout": "research",
    }
    # A control shows its module, whose docstring states its rules.
    assert "SwingPullback (control)" in strategy_source("swing-pullback")


def test_a_stop_at_or_above_the_entry_is_never_signalled() -> None:
    # A guard: none of these patterns should produce one, but a long trade needs it.
    s = TrendlineBreakout()
    s.brackets(Path((0, 100.0), (5, 105)).frame())
    assert not s._signal(5, 105.0)
    assert not s._enter[5]
