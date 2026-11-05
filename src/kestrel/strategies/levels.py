"""Horizontal level retest (research) and its control, the same breakout bought at once.

Level: the most recent known swing high plus every other known swing high from the last
`lookback_bars` bars within `level_tol_atr_pct`% of an ATR of it. With at least two such
swing highs, the level is their average. A breakout is a close more than
`breakout_atr_pct`% of an ATR above the level; one setup per level.

LevelRetest: within `retest_bars` after the breakout, a bar whose low comes within
`touch_atr_pct`% of an ATR of the level (or below it) and that closes above the level.
Stop at the level minus one ATR; target `target_r` x the risk. A close below that stop
level before the retest cancels the setup.

LevelBreakout (control): buys the breakout close itself, same stop rule and target. The
retest is the claimed edge, so LevelRetest has to beat this.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from kestrel.strategies.geometry import Swings, atr


@dataclass
class _Level:
    price: float
    anchor: int
    """Bar of the swing high that defines it, so each level is used once."""


def _frame(enter: list[bool], stop: list[float | None], target: list[float | None]) -> pl.DataFrame:
    return pl.DataFrame(
        {"enter": enter, "stop": stop, "target": target},
        schema={"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64},
    )


class _LevelBase:
    retest: bool = True

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        swing_bars: int = 3,
        level_tol_atr_pct: int = 50,
        breakout_atr_pct: int = 50,
        touch_atr_pct: int = 25,
        retest_bars: int = 30,
        lookback_bars: int = 120,
        target_r: int = 3,
    ) -> None:
        self.symbol = symbol
        self.swing_bars, self.target_r = swing_bars, target_r
        self.level_tol = level_tol_atr_pct / 100
        self.breakout = breakout_atr_pct / 100
        self.touch = touch_atr_pct / 100
        self.retest_bars, self.lookback_bars = retest_bars, lookback_bars

    def _level(self, swings: Swings, t: int, a: float) -> _Level | None:
        recent = [s for s in swings.highs if s[0] >= t - self.lookback_bars]
        if not recent:
            return None
        anchor_at, anchor = recent[-1]
        near = [p for _, p in recent if abs(p - anchor) <= self.level_tol * a]
        return _Level(sum(near) / len(near), anchor_at) if len(near) >= 2 else None

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        high, low, close = bars["high"].to_list(), bars["low"].to_list(), bars["close"].to_list()
        n = len(close)
        atrs = atr(high, low, close)
        swings = Swings(high, low, self.swing_bars)
        enter = [False] * n
        stop: list[float | None] = [None] * n
        target: list[float | None] = [None] * n
        pending: tuple[_Level, int] | None = None  # (level, bar of the breakout)
        used: set[int] = set()

        for t in range(n):
            swings.update(t)
            a = atrs[t]
            if a is None:
                continue
            if pending is not None:
                waiting, broke = pending
                if close[t] < waiting.price - a or t - broke > self.retest_bars:
                    pending = None
                elif low[t] <= waiting.price + self.touch * a and close[t] > waiting.price:
                    self._signal(t, close[t], waiting.price - a, enter, stop, target)
                    pending = None
                    continue
            if pending is None:
                level = self._level(swings, t, a)
                if (
                    level
                    and level.anchor not in used
                    and close[t] > level.price + self.breakout * a
                ):
                    used.add(level.anchor)
                    if self.retest:
                        pending = (level, t)
                    else:
                        self._signal(t, close[t], level.price - a, enter, stop, target)
        return _frame(enter, stop, target)

    def _signal(
        self,
        t: int,
        entry: float,
        stop_at: float,
        enter: list[bool],
        stop: list[float | None],
        target: list[float | None],
    ) -> None:
        if 0 < stop_at < entry:
            enter[t], stop[t] = True, stop_at
            target[t] = entry + self.target_r * (entry - stop_at)


class LevelRetest(_LevelBase):
    retest = True

    def __init__(self, symbol: str = "BTCUSDT", **params: int) -> None:
        super().__init__(symbol, **params)
        self.name = f"level retest {self.target_r}R {symbol}"


class LevelBreakout(_LevelBase):
    retest = False

    def __init__(self, symbol: str = "BTCUSDT", **params: int) -> None:
        params.pop("touch_atr_pct", None)
        params.pop("retest_bars", None)
        super().__init__(symbol, **params)
        self.name = f"level breakout (control) {self.target_r}R {symbol}"
