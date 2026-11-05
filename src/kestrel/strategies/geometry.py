"""Building blocks for chart-geometry strategies: ATR, swings known without hindsight,
and lines through swing points.

Hindsight is the trap with trendlines and levels: on a finished chart a line through two
swing lows uses swing points that weren't confirmed when a trade would have been taken.
Here a swing at bar j only becomes known at bar j + k, after the k bars that confirm it
have closed, and every line or level is built from known swings only.
"""

from __future__ import annotations

from collections.abc import Sequence

ATR_BARS = 14

Point = tuple[int, float]
"""(bar index, price)."""


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float | None]:
    """Wilder's average true range over ATR_BARS bars; None until enough bars have closed.

    Each value uses only that bar and earlier ones."""
    n = len(close)
    out: list[float | None] = [None] * n
    value: float | None = None
    trs: list[float] = []
    for t in range(n):
        tr = high[t] - low[t]
        if t > 0:
            tr = max(tr, abs(high[t] - close[t - 1]), abs(low[t] - close[t - 1]))
        if value is None:
            trs.append(tr)
            if len(trs) == ATR_BARS:
                value = sum(trs) / ATR_BARS
        else:
            value = (value * (ATR_BARS - 1) + tr) / ATR_BARS
        out[t] = value
    return out


class Swings:
    """Swing highs and lows as they become known, bar by bar.

    Bar j is a swing high if its high is above the k bars before it and not below the k
    bars after it (mirror image for swing lows). Call ``update(t)`` once per bar, in order;
    ``highs`` and ``lows`` then hold every swing known at bar t's close."""

    def __init__(self, high: Sequence[float], low: Sequence[float], k: int) -> None:
        self.high, self.low, self.k = high, low, k
        self.highs: list[Point] = []
        self.lows: list[Point] = []

    def update(self, t: int) -> None:
        j, k = t - self.k, self.k
        if j < k:
            return
        h, lo = self.high, self.low
        if h[j] > max(h[j - k : j]) and h[j] >= max(h[j + 1 : t + 1]):
            self.highs.append((j, h[j]))
        if lo[j] < min(lo[j - k : j]) and lo[j] <= min(lo[j + 1 : t + 1]):
            self.lows.append((j, lo[j]))


def line_at(a: Point, b: Point, t: float) -> float:
    """Price at bar t on the straight line through swing points a and b."""
    (i1, p1), (i2, p2) = a, b
    return p2 + (p2 - p1) / (i2 - i1) * (t - i2)
