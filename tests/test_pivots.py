"""Pivot reversal on hand-built closes (each bar's low 0.1 below its close, high 0.1
above). Synthetic bars only: the strategy must not be run on market data until its
research note is frozen."""

from __future__ import annotations

import pytest

from kestrel.strategies.pivots import PivotReversal
from kestrel.strategy import STRATEGIES, make_strategy, validated_brackets
from tests.backtest.helpers import bars
from tests.chart import random_walk


def frame(closes: list[float]):  # type: ignore[no-untyped-def]
    opens = [closes[0], *closes[:-1]]
    return bars(
        opens,
        closes=closes,
        highs=[max(o, c) + 0.1 for o, c in zip(opens, closes, strict=True)],
        lows=[min(o, c) - 0.1 for o, c in zip(opens, closes, strict=True)],
    )


# Falls from 100 to 90 (low 89.9), then rises. A 3.5% swing confirms the pivot low at the
# first close at or above 89.9 x 1.035 = 93.0465: the close of 93.5.
DOWN_UP = [100.0, 97, 94, 92, 90, 91, 92, 93, 93.5, 95]


def test_a_pivot_low_is_confirmed_by_the_rebound_and_bought_then() -> None:
    b = validated_brackets(PivotReversal(), frame(DOWN_UP))
    assert b["enter"].arg_true().to_list() == [8]
    assert b["stop"][8] == pytest.approx(93.5 * 0.985)
    assert b["target"][8] == pytest.approx(93.5 * 1.03)


def test_a_smaller_rebound_is_not_a_pivot() -> None:
    assert validated_brackets(PivotReversal(), frame(DOWN_UP[:8]))["enter"].sum() == 0


def test_after_a_pivot_high_the_next_low_signals_again() -> None:
    # From 95 up to 100 (high 100.1); a close at or below 100.1 x 0.965 = 96.5965 confirms
    # the pivot high; then a new low at 94 (93.9) and a close at or above 97.1865.
    closes = [*DOWN_UP, 98, 100, 98, 96.5, 95, 94, 96, 97.5]
    entries = validated_brackets(PivotReversal(), frame(closes))["enter"].arg_true().to_list()
    assert entries == [8, len(closes) - 1]


def test_no_second_entry_without_a_pivot_high_between() -> None:
    # Rising on: no pivot high is confirmed, so no new low is searched for.
    closes = [*DOWN_UP, 97, 99, 101, 103]
    assert validated_brackets(PivotReversal(), frame(closes))["enter"].sum() == 1


def test_an_immediate_rise_from_the_start_is_a_pivot_low() -> None:
    entries = validated_brackets(PivotReversal(), frame([100.0, 102, 104]))["enter"]
    assert entries.arg_true().to_list() == [2]  # 104 >= 99.9 x 1.035


def test_no_lookahead_on_a_random_walk() -> None:
    b = validated_brackets(make_strategy("pivot-reversal", "ETHUSDT"), random_walk())  # type: ignore[arg-type]
    assert b["enter"].sum() > 0


def test_registered_as_research() -> None:
    spec = STRATEGIES["pivot-reversal"]
    assert spec.kind == "research"
    assert [p.name for p in spec.params] == ["swing_bps", "stop_bps", "target_bps"]
