"""Tori Trades' trendline method on a hand-built chart: a falling line touched three times,
a close through it (entry), a rising trend, then a close below its rising line (exit). A
line touched only twice doesn't count, and no decision uses a bar after it."""

from __future__ import annotations

from datetime import timedelta

import polars as pl

from kestrel.strategies.tori import ToriTrendline
from tests.backtest.helpers import bars

STEP = timedelta(hours=4)


def chart(peaks: int = 4) -> pl.DataFrame:
    """Falling peaks every 10 bars on the line 130 - 0.2 t; a break at bar 10 x peaks + 2;
    then a rising zigzag; then a fall."""
    highs, lows, closes = [], [], []
    turn = 10 * peaks + 2
    for t in range(turn):
        line = 130 - 0.2 * t
        depth = abs((t % 10) - 5) / 5  # 0 at the peaks (bars 5, 15, ...), 1 between
        h = line - 4 * depth
        highs.append(h)
        lows.append(h - 2)
        closes.append(h - 1)
    base = closes[-1]
    for i in range(40):  # up 0.6 a bar, a zigzag of period 8 makes rising swing lows
        mid = base + 3 + 0.6 * i - 6 * (1 - abs((i % 8) - 4) / 4)
        highs.append(mid + 1)
        lows.append(mid - 1)
        closes.append(mid)
    top = closes[-1]
    for i in range(15):  # a fall: closes well below any rising line
        c = top - 3 * (i + 1)
        highs.append(c + 1)
        lows.append(c - 1)
        closes.append(c)
    opens = [closes[0], *closes[:-1]]
    return bars(opens, closes, highs=highs, lows=lows, step=STEP)


def test_a_break_of_a_three_touch_line_enters_and_a_break_of_the_new_line_exits() -> None:
    b = chart()
    out = ToriTrendline("BTCUSDT").brackets(b)
    entries = [i for i, e in enumerate(out["enter"].to_list()) if e]
    exits = [i for i, x in enumerate(out["exit"].to_list()) if x]
    assert len(entries) == 1
    t = entries[0]
    assert 42 <= t <= 50  # just after the falling peaks end (bar 42 turns up)
    assert out["stop"][t] < b["close"][t]
    assert out["target"].null_count() == len(out)  # no profit target
    assert len(exits) == 1
    assert exits[0] > 82  # once the fall starts, not during the rise


def test_a_line_needs_its_touches_and_no_close_above_it() -> None:
    s = ToriTrendline("BTCUSDT", min_touches=3, touch_atr_pct=50)
    closes = [100.0] * 60
    two = [(5, 129.0), (15, 127.0)]  # on the line 130 - 0.2 t
    assert s.resistance(two, closes, 30, atr_now=2.0) is None  # only two touches
    three = [(5, 129.0), (15, 127.0), (25, 125.0)]
    assert s.resistance(three, closes, 30, atr_now=2.0) == ((5, 129.0), (25, 125.0))
    off = [(5, 129.0), (15, 120.0), (25, 125.0)]  # the middle high is far below the line
    assert s.resistance(off, closes, 30, atr_now=2.0) is None
    broken = [*closes]
    broken[20] = 130.0  # a close above the line before the signal: it isn't resistance
    assert s.resistance(three, broken, 30, atr_now=2.0) is None


def test_no_lookahead() -> None:
    b = chart()
    full = ToriTrendline("BTCUSDT").brackets(b)
    for cut in (44, 60, 85, 100):
        part = ToriTrendline("BTCUSDT").brackets(b.head(cut))
        assert part["enter"].to_list() == full["enter"].to_list()[:cut]
        assert part["exit"].to_list() == full["exit"].to_list()[:cut]
