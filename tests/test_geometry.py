from __future__ import annotations

import pytest

from kestrel.strategies.geometry import Swings, atr, line_at


def test_atr_waits_for_14_bars_then_smooths() -> None:
    high, low, close = [101.0] * 15, [99.0] * 15, [100.0] * 15
    high[14], low[14] = 108, 92  # a true range of 16
    out = atr(high, low, close)
    assert out[:13] == [None] * 13
    assert out[13] == 2
    assert out[14] == pytest.approx((13 * 2 + 16) / 14)  # Wilder's smoothing


def test_true_range_includes_a_gap_from_the_previous_close() -> None:
    high, low, close = [101.0] * 15, [99.0] * 15, [100.0] * 15
    high[14], low[14], close[14] = 111, 109, 110  # gaps up 10 from 100
    assert atr(high, low, close)[14] == pytest.approx((13 * 2 + 11) / 14)


def test_a_swing_is_known_only_after_the_bars_that_confirm_it() -> None:
    high = [1.0, 2, 5, 3, 2, 4, 6, 7]
    low = [0.0, 1, 4, 2, 1, 3, 5, 6]
    s = Swings(high, low, 2)
    for t in range(4):
        s.update(t)
    assert s.highs == []  # bar 2 is the high, but bars 3 and 4 haven't both closed
    s.update(4)
    assert s.highs == [(2, 5.0)]
    for t in range(5, 8):
        s.update(t)
    assert s.lows == [(4, 1.0)]  # known at bar 6


def test_an_equal_bar_before_is_not_a_swing_but_after_is() -> None:
    s = Swings([1.0, 5, 5, 1, 1], [0.0] * 5, 1)
    for t in range(5):
        s.update(t)
    assert s.highs == [(1, 5.0)]  # bar 2 ties bar 1, so only bar 1 counts


def test_line_at_extends_the_line() -> None:
    assert line_at((0, 10.0), (10, 20.0), 15) == 25
    assert line_at((0, 20.0), (10, 10.0), 12) == 8
