"""Trendline strategies (research) and the pullback control.

Every line runs through two known swing points (see geometry.Swings) and is projected
forward to the current bar. Tolerances are a percentage of the ATR at the signal bar.

TrendlineBounce (research). Uptrend: the two most recent known swing lows are rising and
the two most recent known swing highs are rising. Line: through those two swing lows.
Signal: a bar whose low comes within `touch_atr_pct`% of an ATR of the line (or below it)
and that closes above the line. Stop: the line minus `stop_atr_pct`% of an ATR. A close
more than that below the line breaks it; there is no new line until a new swing low forms.

SwingPullback (control). The same uptrend, without the line: a bar whose low comes within
`pullback_atr_pct`% of an ATR of the most recent known swing low and that closes above it.
Stop: that swing low minus `stop_atr_pct`% of an ATR. The line is the claimed edge, so
TrendlineBounce has to beat this.

TrendlineBreakout (research). Line: through the two most recent known swing highs, the
later one lower. Signal: a close more than `breakout_atr_pct`% of an ATR above the line.
Stop: the lowest low since the later swing high. One entry per line.

WedgeBreakout (research). Upper line: the two most recent known swing highs, the later one
lower. Lower line: the two most recent known swing lows, the later one lower. A wedge when
both fall and the gap between the lines at the latest of the four swings is at most
`converge_pct`% of the gap at the earliest. Signal: a close more than `breakout_atr_pct`%
of an ATR above the upper line, while price hasn't closed below the lower line. Stop: the
latest swing low. One entry per wedge. It has to beat TrendlineBreakout: convergence is the
claimed edge.

All take a `target_r` x risk target from the signal close and signal at a bar's close; the
engine buys at the next open.
"""

from __future__ import annotations

import polars as pl

from kestrel.strategies.geometry import Point, Swings, atr, line_at


class _Base:
    def __init__(self, symbol: str, swing_bars: int, target_r: int) -> None:
        self.symbol, self.swing_bars, self.target_r = symbol, swing_bars, target_r

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        high, low, close = bars["high"].to_list(), bars["low"].to_list(), bars["close"].to_list()
        n = len(close)
        self._high, self._low, self._close = high, low, close
        atrs = atr(high, low, close)
        swings = Swings(high, low, self.swing_bars)
        self._enter = [False] * n
        self._stop: list[float | None] = [None] * n
        self._target: list[float | None] = [None] * n
        self._reset()
        for t in range(n):
            swings.update(t)
            a = atrs[t]
            if a is not None:
                self._step(t, a, swings)
        return pl.DataFrame(
            {"enter": self._enter, "stop": self._stop, "target": self._target},
            schema={"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64},
        )

    def _signal(self, t: int, stop_at: float) -> bool:
        entry = self._close[t]
        if not 0 < stop_at < entry:
            return False
        self._enter[t], self._stop[t] = True, stop_at
        self._target[t] = entry + self.target_r * (entry - stop_at)
        return True

    def _reset(self) -> None: ...

    def _step(self, t: int, a: float, swings: Swings) -> None: ...


def _uptrend(swings: Swings) -> tuple[Point, Point] | None:
    """The two latest known swing lows, if both swing lows and swing highs are rising."""
    if len(swings.lows) < 2 or len(swings.highs) < 2:
        return None
    (_, h1), (_, h2) = swings.highs[-2:]
    l1, l2 = swings.lows[-2:]
    return (l1, l2) if l2[1] > l1[1] and h2 > h1 else None


class TrendlineBounce(_Base):
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        swing_bars: int = 3,
        touch_atr_pct: int = 25,
        stop_atr_pct: int = 100,
        target_r: int = 3,
    ) -> None:
        super().__init__(symbol, swing_bars, target_r)
        self.touch, self.stop_buffer = touch_atr_pct / 100, stop_atr_pct / 100
        self.name = f"rising trendline bounce {target_r}R {symbol}"

    def _reset(self) -> None:
        self._broken: tuple[Point, Point] | None = None

    def _step(self, t: int, a: float, swings: Swings) -> None:
        pair = _uptrend(swings)
        if pair is None or pair == self._broken:
            return
        line = line_at(*pair, t)
        if self._close[t] < line - self.stop_buffer * a:
            self._broken = pair  # this line is spent until a new swing low forms
        elif self._low[t] <= line + self.touch * a and self._close[t] > line:
            self._signal(t, line - self.stop_buffer * a)


class SwingPullback(_Base):
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        swing_bars: int = 3,
        pullback_atr_pct: int = 150,
        stop_atr_pct: int = 100,
        target_r: int = 3,
    ) -> None:
        super().__init__(symbol, swing_bars, target_r)
        self.pullback, self.stop_buffer = pullback_atr_pct / 100, stop_atr_pct / 100
        self.name = f"swing pullback (control) {target_r}R {symbol}"

    def _step(self, t: int, a: float, swings: Swings) -> None:
        pair = _uptrend(swings)
        if pair is None:
            return
        swing_low = pair[1][1]
        if self._low[t] <= swing_low + self.pullback * a and self._close[t] > swing_low:
            self._signal(t, swing_low - self.stop_buffer * a)


def _falling(points: list[Point]) -> tuple[Point, Point] | None:
    """The two latest points, if the later one is lower."""
    if len(points) < 2:
        return None
    p1, p2 = points[-2:]
    return (p1, p2) if p2[1] < p1[1] else None


class TrendlineBreakout(_Base):
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        swing_bars: int = 3,
        breakout_atr_pct: int = 50,
        target_r: int = 3,
    ) -> None:
        super().__init__(symbol, swing_bars, target_r)
        self.margin = breakout_atr_pct / 100
        self.name = f"falling trendline breakout {target_r}R {symbol}"

    def _reset(self) -> None:
        self._used: set[tuple[Point, Point]] = set()

    def _step(self, t: int, a: float, swings: Swings) -> None:
        upper = _falling(swings.highs)
        if upper is None or upper in self._used:
            return
        if self._close[t] > line_at(*upper, t) + self.margin * a:
            self._used.add(upper)
            self._signal(t, min(self._low[upper[1][0] : t + 1]))


class WedgeBreakout(_Base):
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        swing_bars: int = 3,
        converge_pct: int = 60,
        breakout_atr_pct: int = 50,
        target_r: int = 3,
    ) -> None:
        super().__init__(symbol, swing_bars, target_r)
        self.converge, self.margin = converge_pct / 100, breakout_atr_pct / 100
        self.name = f"falling wedge breakout {target_r}R {symbol}"

    def _reset(self) -> None:
        self._used: set[tuple[Point, ...]] = set()

    def _step(self, t: int, a: float, swings: Swings) -> None:
        upper, lower = _falling(swings.highs), _falling(swings.lows)
        if upper is None or lower is None:
            return
        wedge = (*upper, *lower)
        if wedge in self._used:
            return
        first = min(p[0] for p in wedge)
        last = max(p[0] for p in wedge)
        gap_first = line_at(*upper, first) - line_at(*lower, first)
        gap_last = line_at(*upper, last) - line_at(*lower, last)
        if not 0 < gap_last <= self.converge * gap_first:
            return  # not converging, or the lines have crossed
        if self._close[t] < line_at(*lower, t):
            self._used.add(wedge)  # broke down instead: this wedge is spent
        elif self._close[t] > line_at(*upper, t) + self.margin * a:
            self._used.add(wedge)
            self._signal(t, lower[1][1])
