"""Structure pullback rules, on a hand-built sequence with swing_bars=1.

    bar:   0    1    2    3    4    5    6    7    8    9
    high: 120  125  119  112  116  111  109  121  128  126
    low:  115  118  110  105  108  100  102  107  117  110

Swing highs: 125 (bar 1), then a lower 116 (bar 4), known at bar 5. Bar 7 closes at 120,
above 116: a change of character. The leg's low is 100 (bar 5); the leg's high is 128
(bar 8). Bars 6 and 8 leave a fair value gap from 109 to 117, midpoint 113.
Retracements of 100..128: 50% = 114, 61.8% = 110.70, 78.6% = 105.99.
Bar 9 dips to 110 (below 61.8% and the gap's midpoint) and closes at 115 (above 78.6%):
the entry signal, with stop 100 and a 4R target of 115 + 4 x 15 = 175.

These tests use synthetic bars only. The strategy must not be run on market data until
its research note is frozen.
"""

from __future__ import annotations

import math
import random

import polars as pl
import pytest

from kestrel.strategies.structure import StructurePullback
from kestrel.strategy import STRATEGIES, strategy_source, validated_brackets
from tests.backtest.helpers import bars

HIGH = [120.0, 125, 119, 112, 116, 111, 109, 121, 128, 126]
LOW = [115.0, 118, 110, 105, 108, 100, 102, 107, 117, 110]
CLOSE = [116.0, 122, 112, 107, 110, 102, 108, 120, 127, 115]


def frame(
    high: list[float] = HIGH, low: list[float] = LOW, close: list[float] = CLOSE
) -> pl.DataFrame:
    opens = [close[0], *close[:-1]]
    return bars(opens, closes=close, highs=high, lows=low)


def signals(f: pl.DataFrame, **params: int) -> pl.DataFrame:
    return validated_brackets(StructurePullback(swing_bars=1, **params), f)


def entries(f: pl.DataFrame, **params: int) -> list[int]:
    return signals(f, **params)["enter"].arg_true().to_list()


def test_the_full_setup_signals_once_with_stop_and_target() -> None:
    b = signals(frame())
    assert b["enter"].arg_true().to_list() == [9]
    assert b["stop"][9] == 100.0
    assert b["target"][9] == pytest.approx(175.0)


def test_the_target_follows_target_r() -> None:
    assert signals(frame(), target_r=2)["target"][9] == pytest.approx(145.0)


def test_no_fair_value_gap_no_entry() -> None:
    low = LOW.copy()
    low[8] = 108  # bar 8's low no longer clears bar 6's high (109)
    assert entries(frame(low=low)) == []


def test_a_shallow_pullback_is_not_an_entry() -> None:
    low = LOW.copy()
    low[9] = 112  # above the 61.8% retracement (110.70)
    assert entries(frame(low=low)) == []


def test_closing_below_the_deep_level_is_not_an_entry() -> None:
    close = CLOSE.copy()
    close[9] = 105  # below the 78.6% retracement (105.99): the pullback didn't hold
    assert entries(frame(close=close)) == []


def test_no_lower_high_no_change_of_character() -> None:
    high = HIGH.copy()
    # Lower bars 1 and 2 so neither is a swing high above 116. (Lowering only bar 1 lets
    # bar 2, at 119, become the earlier swing high: still a lower high, still a setup.)
    high[1], high[2] = 114, 113
    assert entries(frame(high=high)) == []


def test_closing_below_the_leg_low_cancels_the_setup() -> None:
    high, low, close = [*HIGH, 118.0, 115], [*LOW, 96.0, 104], [*CLOSE, 97.0, 110]
    low[9], close[9] = 113, 116  # no entry on bar 9
    # Bar 10 closes below 100, so bar 11's dip into the zone must not trigger.
    assert entries(frame(high=high, low=low, close=close)) == []


def test_setups_expire() -> None:
    assert entries(frame(), expiry_bars=1) == []


def test_no_lookahead_on_a_random_walk() -> None:
    rnd = random.Random(3)
    price, o, c, h, lo = 100.0, [], [], [], []
    for _ in range(1500):
        op = price
        cl = op * math.exp(rnd.gauss(0, 0.015))
        o.append(op)
        c.append(cl)
        h.append(max(op, cl) * (1 + abs(rnd.gauss(0, 0.006))))
        lo.append(min(op, cl) * (1 - abs(rnd.gauss(0, 0.006))))
        price = cl
    b = validated_brackets(
        StructurePullback(), bars(o, closes=c, highs=h, lows=lo)
    )  # raises on lookahead
    # It does find setups in noise; whether they make money is for the frozen note to test.
    assert b["enter"].sum() > 0


def test_registered_as_research_with_its_rules_visible() -> None:
    spec = STRATEGIES["structure-pullback"]
    assert spec.kind == "research"
    assert [p.name for p in spec.params] == ["swing_bars", "target_r", "expiry_bars"]
    source = strategy_source("structure-pullback")
    assert "Change of character (bullish)" in source
    assert "class StructurePullback" in source
