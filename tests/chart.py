"""Hand-built price paths for the chart-geometry strategies.

A zigzag moves the close at most 1 a bar between turning points, with every bar's high
1 above its close and low 1 below. Every true range is then exactly 2, so the ATR is 2
from bar 13 on, and expected levels, lines, stops and targets can be worked out by hand.
"""

from __future__ import annotations

import itertools
import math
import random

import polars as pl

from tests.backtest.helpers import bars


class Path:
    def __init__(self, *turns: tuple[int, float]) -> None:
        self.close: list[float] = []
        for (i1, p1), (i2, p2) in itertools.pairwise(turns):
            step = (p2 - p1) / (i2 - i1)
            assert abs(step) <= 1, "a zigzag moves at most 1 a bar"
            self.close += [p1 + step * k for k in range(i2 - i1)]
        self.close.append(turns[-1][1])
        self.high = [c + 1 for c in self.close]
        self.low = [c - 1 for c in self.close]

    def bar(self, t: int, close: float, high: float, low: float) -> Path:
        """Replace one bar. Its open is the previous close, so keep that inside high-low."""
        self.close[t], self.high[t], self.low[t] = close, high, low
        return self

    def frame(self, n: int | None = None) -> pl.DataFrame:
        c, h, lo = self.close[:n], self.high[:n], self.low[:n]
        opens = [c[0], *c[:-1]]
        assert all(lo[i] <= opens[i] <= h[i] for i in range(len(c)))
        return bars(opens, closes=c, highs=h, lows=lo)


def random_walk(n: int = 1500, seed: int = 3) -> pl.DataFrame:
    rnd = random.Random(seed)
    price, o, c, h, lo = 100.0, [], [], [], []
    for _ in range(n):
        op = price
        cl = op * math.exp(rnd.gauss(0, 0.015))
        o.append(op)
        c.append(cl)
        h.append(max(op, cl) * (1 + abs(rnd.gauss(0, 0.006))))
        lo.append(min(op, cl) * (1 - abs(rnd.gauss(0, 0.006))))
        price = cl
    return bars(o, closes=c, highs=h, lows=lo)
