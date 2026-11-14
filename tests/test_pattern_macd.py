"""Pattern + MACD turn, on hand-built 4h bars only: the strategy must not be run on market
data until its research note is frozen.

Forty bars fall 1 a bar from 140 to 101 (MACD needs about 35 to warm up). Bar 40 is a pin
bar at the low: open 100.5, high 101, low 95, close 100.8. Bar 41 closes at 102, above the
pin's high, as MACD turns up: entry, stop 95, 2R target 102 + 2 x 7 = 116.
"""

from __future__ import annotations

import pytest

from kestrel.strategies.pattern_macd import PatternMacd, bullish_pattern
from kestrel.strategy import STRATEGIES, validated_brackets
from tests.backtest.helpers import bars

FALL = [140.0 - i for i in range(40)]  # closes 140..101
PIN = (100.5, 101.0, 95.0, 100.8)  # open, high, low, close


def frame(after: list[tuple[float, float, float, float]]):  # type: ignore[no-untyped-def]
    opens = [FALL[0], *FALL[:-1]] + [b[0] for b in after]
    closes = FALL + [b[3] for b in after]
    highs = [max(o, c) for o, c in zip(opens[:40], FALL, strict=True)] + [b[1] for b in after]
    lows = [min(o, c) for o, c in zip(opens[:40], FALL, strict=True)] + [b[2] for b in after]
    return bars(opens, closes=closes, highs=highs, lows=lows)


def test_a_pin_bar_at_the_low_then_a_breakout_with_macd_turning_up() -> None:
    b = validated_brackets(PatternMacd(), frame([PIN, (100.8, 102.2, 100.5, 102.0)]))
    assert b["enter"].arg_true().to_list() == [41]
    assert (b["stop"][41], b["target"][41]) == (95.0, pytest.approx(116.0))


def test_the_breakout_must_come_within_the_expiry() -> None:
    wait = [(100.8, 100.9, 99.0, 100.0)] * 4  # no close above 101, no new low
    late = frame([PIN, *wait, (100.0, 102.2, 99.8, 102.0)])
    assert not validated_brackets(PatternMacd(), late)["enter"].any()
    assert validated_brackets(PatternMacd(expiry_bars=5), late)["enter"].arg_true().to_list() == [
        45
    ]


def test_the_patterns() -> None:
    o, h, lo, c = [10.0, 9.5], [11.0, 10.5], [9.0, 9.2], [9.2, 10.4]
    assert bullish_pattern(o, h, lo, c, 1)  # inside bar (and engulfing's body test fails)
    assert bullish_pattern([10.0, 8.9], [11.0, 11.5], [9.0, 8.5], [9.2, 11.2], 1)  # outside
    assert bullish_pattern([10.0, 9.1], [10.2, 10.6], [9.0, 9.05], [9.2, 10.5], 1)  # engulfing
    assert not bullish_pattern([10.0, 9.5], [10.2, 11.0], [9.0, 9.4], [9.2, 9.6], 1)  # none
    assert bullish_pattern([10.0], [10.1], [7.0], [10.05], 0)  # a pin bar needs no neighbour


def test_registered_as_a_bracket_research_strategy() -> None:
    spec = STRATEGIES["pattern-macd"]
    assert spec.kind == "research"
    assert [p.name for p in spec.params] == ["swing_bars", "expiry_bars", "target_r"]
