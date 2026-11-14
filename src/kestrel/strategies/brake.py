"""A crash brake: a trailing stop laid over a strategy's target weights (research).

Weekly momentum holds from Sunday to Sunday whatever happens in between, and
trend-vol-target leaves only when its trend votes turn, which can take weeks. The brake
asks whether selling early in a sharp fall does better than sitting through it.

While a position is held, track the highest close since it was entered. If a day closes
more than ``brake_atr`` x ATR(14) below that high (a chandelier exit), the brake trips: the
target goes to 0, filled at the next open like every other decision, and stays 0 until the
strategy's next decision (weekly momentum: the next Sunday; trend-vol-target: the next
change in its number of up-trends). Then the strategy's own target applies again.

Closes only, never the bar's low: the brake decides at a close and fills at the next
open, so it needs nothing new from the engines and can't see inside a bar it hasn't
finished. A stop inside the bar would react sooner and is a different test.

The risk it carries is the opposite of the one it targets: crypto often falls by a few
ATRs and recovers within days, and a brake that sells the dip and misses the rebound pays
two fees for a worse result. The research note fixes one multiple in advance.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from kestrel.indicators import atr
from kestrel.strategies.trend import TrendVolTarget
from kestrel.strategies.weekly import WeeklyMomentum

ATR_BARS = 14


def brake(
    close: Sequence[float],
    atr_values: Sequence[float | None],
    weights: Sequence[float],
    decisions: Sequence[bool],
    multiple: float,
) -> list[float]:
    """``weights`` with the brake applied. Everything at index t uses closes up to t."""
    out: list[float] = []
    high: float | None = None
    tripped = False
    for c, a, w, decided in zip(close, atr_values, weights, decisions, strict=True):
        if decided:
            tripped = False  # the strategy has decided again: its own target applies
        if tripped or w <= 0:
            out.append(0.0)
            if w <= 0:
                high = None
            continue
        high = c if high is None else max(high, c)
        if a is not None and c < high - multiple * a:
            tripped, high = True, None
            out.append(0.0)
            continue
        out.append(w)
    return out


def _check(multiple: int) -> None:
    if multiple < 1:
        raise ValueError("brake_atr must be at least 1")


class WeeklyMomentumBrake(WeeklyMomentum):
    def __init__(self, symbol: str = "ETHUSDT", brake_atr: int = 2) -> None:
        _check(brake_atr)
        super().__init__(symbol)
        self.multiple = brake_atr
        self.name = f"weekly momentum {symbol}, brake {brake_atr} ATR"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        base = super().target_weights(bars)
        sundays = (bars["open_time"].dt.weekday() == 7).to_list()
        return _apply(bars, base, sundays, self.multiple)


class TrendVolTargetBrake(TrendVolTarget):
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        fast_days: int = 20,
        mid_days: int = 60,
        slow_days: int = 120,
        vol_days: int = 30,
        target_vol_pct: int = 50,
        brake_atr: int = 2,
    ) -> None:
        _check(brake_atr)
        super().__init__(symbol, fast_days, mid_days, slow_days, vol_days, target_vol_pct)
        self.multiple = brake_atr
        self.name = f"{self.name}, brake {brake_atr} ATR"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        base = super().target_weights(bars)
        votes = self.vote_count(bars)
        changed = (votes != votes.shift(1)).fill_null(False).to_list()
        return _apply(bars, base, changed, self.multiple)


def _apply(bars: pl.DataFrame, base: pl.Series, decisions: list[bool], multiple: int) -> pl.Series:
    if bars.height == 0:
        return base
    a = atr(bars, ATR_BARS).to_list()
    braked = brake(bars["close"].to_list(), a, base.to_list(), decisions, multiple)
    return pl.Series("weight", braked, dtype=pl.Float64)
