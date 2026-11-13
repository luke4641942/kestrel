"""Technical indicators: one tested implementation each, shared by strategies and charts.

Every value at bar t uses only bars up to and including t (closed bars), so an indicator
can never leak the future into a strategy; ``tests/test_indicators.py`` checks this for
each one by recomputing on truncated histories. Values are null until an indicator has
enough history.

Lengths are in bars, the charting convention: RSI 14 on a 4h chart is 14 four-hour bars.
The one exception is realised volatility, whose window is in days and is annualised, so
it reads the same on any bar size.

RSI and ATR use Wilder's smoothing seeded with a simple average of the first ``n`` values,
the textbook method, so they match charting software. EMAs are seeded the same way (an
SMA of the first ``n`` values), then ``alpha = 2 / (n + 1)``.

``PRESETS`` is the fixed menu the dashboard draws, at textbook settings (SMA 50 and 200,
RSI 14, MACD 12/26/9...). There is deliberately no way to choose other lengths on a chart:
an indicator is a way to look at an idea, and tuning its settings to past prices is how a
backtest fools you. A strategy that uses one states its setting in a frozen research note.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import polars as pl

DAYS_PER_YEAR = 365


def _series(name: str, values: Sequence[float | None]) -> pl.Series:
    return pl.Series(name, list(values), dtype=pl.Float64)


def sma(values: pl.Series, n: int) -> pl.Series:
    """Simple moving average of the last ``n`` values."""
    return values.cast(pl.Float64).rolling_mean(n).alias(f"sma{n}")


def _seeded(values: Sequence[float | None], n: int, alpha: float) -> list[float | None]:
    """Exponential smoothing seeded with the simple average of the first ``n`` values.
    Leading nulls are skipped (an input that is itself still warming up)."""
    out: list[float | None] = [None] * len(values)
    seed: list[float] = []
    value: float | None = None
    for i, v in enumerate(values):
        if v is None:
            continue
        if value is None:
            seed.append(v)
            if len(seed) == n:
                value = sum(seed) / n
        else:
            value = value + alpha * (v - value)
        out[i] = value
    return out


def ema(values: pl.Series, n: int) -> pl.Series:
    """Exponential moving average, ``alpha = 2 / (n + 1)``, seeded with an SMA."""
    return _series(f"ema{n}", _seeded(values.cast(pl.Float64).to_list(), n, 2 / (n + 1)))


def wilder(values: Sequence[float | None], n: int) -> list[float | None]:
    """Wilder's smoothing (``alpha = 1 / n``), seeded with an SMA of the first ``n``."""
    return _seeded(values, n, 1 / n)


def rsi(close: pl.Series, n: int = 14) -> pl.Series:
    """Relative strength index (Wilder): 100 - 100 / (1 + average gain / average loss).
    100 when there were no losses, 50 when the price didn't move at all."""
    c = close.cast(pl.Float64).to_list()
    gains: list[float | None] = [None] + [max(b - a, 0.0) for a, b in itertools.pairwise(c)]
    losses: list[float | None] = [None] + [max(a - b, 0.0) for a, b in itertools.pairwise(c)]
    g, lo = wilder(gains, n), wilder(losses, n)
    out: list[float | None] = []
    for up, down in zip(g, lo, strict=True):
        if up is None or down is None:
            out.append(None)
        elif down == 0:
            out.append(50.0 if up == 0 else 100.0)
        else:
            out.append(100 - 100 / (1 + up / down))
    return _series(f"rsi{n}", out)


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    """Each bar's range, widened to include a gap from the previous close."""
    out = []
    for t in range(len(close)):
        tr = high[t] - low[t]
        if t > 0:
            tr = max(tr, abs(high[t] - close[t - 1]), abs(low[t] - close[t - 1]))
        out.append(tr)
    return out


def atr(bars: pl.DataFrame, n: int = 14) -> pl.Series:
    """Average true range (Wilder)."""
    tr = true_range(bars["high"].to_list(), bars["low"].to_list(), bars["close"].to_list())
    return _series(f"atr{n}", wilder(list(tr), n))


@dataclass(frozen=True)
class Macd:
    line: pl.Series
    """Fast EMA minus slow EMA."""
    signal: pl.Series
    """EMA of the line."""
    histogram: pl.Series
    """Line minus signal."""


def macd(close: pl.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Macd:
    line = ema(close, fast) - ema(close, slow)
    sig = _series("signal", _seeded(line.to_list(), signal, 2 / (signal + 1)))
    return Macd(line.alias("macd"), sig, (line - sig).alias("histogram"))


@dataclass(frozen=True)
class Bands:
    middle: pl.Series
    upper: pl.Series
    lower: pl.Series


def bollinger(close: pl.Series, n: int = 20, k: float = 2.0) -> Bands:
    """An SMA with bands ``k`` population standard deviations either side."""
    mid = sma(close, n)
    sd = close.cast(pl.Float64).rolling_std(n, ddof=0)
    return Bands(mid.alias("middle"), (mid + k * sd).alias("upper"), (mid - k * sd).alias("lower"))


def rate_of_change(close: pl.Series, n: int) -> pl.Series:
    """Return over the last ``n`` bars, as a fraction."""
    c = close.cast(pl.Float64)
    return (c / c.shift(n) - 1).alias(f"roc{n}")


def ma_distance(close: pl.Series, n: int) -> pl.Series:
    """How far the close is above (+) or below (-) its ``n``-bar SMA, as a fraction."""
    return (close.cast(pl.Float64) / sma(close, n) - 1).alias(f"ma_distance{n}")


def bars_per_day(bars: pl.DataFrame) -> float:
    """From the median bar spacing: robust to the odd missing bar."""
    step = bars["open_time"].diff().median()
    if not isinstance(step, timedelta) or step <= timedelta(0):
        raise ValueError("need at least two bars with increasing open times")
    return timedelta(days=1) / step


def realised_volatility(bars: pl.DataFrame, days: int = 30) -> pl.Series:
    """Annualised standard deviation of log returns over the last ``days`` days."""
    per_day = bars_per_day(bars)
    window = max(2, round(days * per_day))
    c = bars["close"].cast(pl.Float64)
    log_return = (c / c.shift(1)).log()
    return (log_return.rolling_std(window) * math.sqrt(DAYS_PER_YEAR * per_day)).alias(
        f"volatility{days}d"
    )


def relative_volume(volume: pl.Series, n: int = 20) -> pl.Series:
    """This bar's volume over the average of the ``n`` bars before it (1 = normal)."""
    v = volume.cast(pl.Float64)
    return (v / v.shift(1).rolling_mean(n)).alias(f"rvol{n}")


def on_balance_volume(close: pl.Series, volume: pl.Series) -> pl.Series:
    """Running total of volume, added on up closes and subtracted on down closes. Its
    level is arbitrary (it starts at 0); only its direction means anything."""
    c, v = close.to_list(), volume.cast(pl.Float64).to_list()
    total, out = 0.0, [0.0]
    for t in range(1, len(c)):
        if c[t] > c[t - 1]:
            total += v[t]
        elif c[t] < c[t - 1]:
            total -= v[t]
        out.append(total)
    return _series("obv", out[: len(c)])


# ---------- the dashboard's menu ----------

Pane = Literal["price", "own"]
"""Drawn over the price, or in its own pane underneath."""


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    pane: Pane
    description: str
    compute: Callable[[pl.DataFrame], dict[str, pl.Series]]
    lines: tuple[str, ...] = ("value",)
    """Names of the series ``compute`` returns, in drawing order."""
    guides: tuple[float, ...] = ()
    """Horizontal reference levels (RSI's 30 and 70)."""
    percent: bool = False
    """Values are fractions, shown as %."""


def _one(fn: Callable[[pl.DataFrame], pl.Series]) -> Callable[[pl.DataFrame], dict[str, pl.Series]]:
    return lambda bars: {"value": fn(bars)}


def _macd(bars: pl.DataFrame) -> dict[str, pl.Series]:
    m = macd(bars["close"])
    return {"macd": m.line, "signal": m.signal, "histogram": m.histogram}


def _bollinger(bars: pl.DataFrame) -> dict[str, pl.Series]:
    b = bollinger(bars["close"])
    return {"upper": b.upper, "middle": b.middle, "lower": b.lower}


PRESETS: dict[str, Preset] = {
    p.key: p
    for p in (
        Preset(
            "sma50",
            "SMA 50",
            "price",
            "Simple average of the last 50 closes.",
            _one(lambda b: sma(b["close"], 50)),
        ),
        Preset(
            "sma200",
            "SMA 200",
            "price",
            "Simple average of the last 200 closes: the long-term trend.",
            _one(lambda b: sma(b["close"], 200)),
        ),
        Preset(
            "ema20",
            "EMA 20",
            "price",
            "Exponential average of the last 20 closes.",
            _one(lambda b: ema(b["close"], 20)),
        ),
        Preset(
            "bollinger",
            "Bollinger 20, 2",
            "price",
            "SMA 20 with bands 2 standard deviations either side: wide when the market is wild.",
            _bollinger,
            lines=("upper", "middle", "lower"),
        ),
        Preset(
            "rsi14",
            "RSI 14",
            "own",
            "Relative strength index: 0-100, the share of recent movement that was up. "
            "Above 70 is called overbought, below 30 oversold.",
            _one(lambda b: rsi(b["close"], 14)),
            guides=(30.0, 70.0),
        ),
        Preset(
            "macd",
            "MACD 12, 26, 9",
            "own",
            "EMA 12 minus EMA 26, with a 9-bar EMA of that as its signal line.",
            _macd,
            lines=("macd", "signal", "histogram"),
            guides=(0.0,),
        ),
        Preset(
            "atr14",
            "ATR 14",
            "own",
            "Average true range: how far the price typically moves in a bar.",
            _one(lambda b: atr(b, 14)),
        ),
        Preset(
            "volatility30",
            "Volatility 30d",
            "own",
            "Annualised volatility of the last 30 days of returns.",
            _one(lambda b: realised_volatility(b, 30)),
            percent=True,
        ),
        Preset(
            "rvol20",
            "Relative volume 20",
            "own",
            "This bar's volume over the average of the 20 before it: 1 is normal, 3 is triple.",
            _one(lambda b: relative_volume(b["volume"], 20)),
            guides=(1.0,),
        ),
        Preset(
            "obv",
            "On-balance volume",
            "own",
            "Volume added on up closes, subtracted on down closes. Only its direction matters.",
            _one(lambda b: on_balance_volume(b["close"], b["volume"])),
        ),
        Preset(
            "ma_distance200",
            "Distance from SMA 200",
            "own",
            "How far the close is above or below its 200-bar average.",
            _one(lambda b: ma_distance(b["close"], 200)),
            guides=(0.0,),
            percent=True,
        ),
    )
}


def compute_presets(bars: pl.DataFrame, keys: Sequence[str]) -> dict[str, dict[str, pl.Series]]:
    """Each requested preset's lines over ``bars``."""
    unknown = [k for k in keys if k not in PRESETS]
    if unknown:
        raise ValueError(f"unknown indicator(s) {', '.join(unknown)}; one of {', '.join(PRESETS)}")
    return {k: PRESETS[k].compute(bars) for k in dict.fromkeys(keys)}
