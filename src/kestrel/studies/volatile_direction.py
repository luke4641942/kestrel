"""Which way do volatile spells go? A raw-effect study of MACD's direction in them.

The regime model (kestrel.regimes) tells us *when* big moves are likely: its volatile state
persists for weeks. It says nothing about direction. Two readings of the research disagree:

- **trend**: the direction MACD shows (its histogram above or below zero) continues;
- **rebound**: in high-volatility "panic" states moves snap back, which is why momentum
  crashes there (Daniel and Moskowitz, Momentum Crashes, 2016).

So, on days the regime model's filtered state is volatile (``regime_volatile`` is 1), with
the MACD 12/26/9 histogram's sign at that day's close as the signal, the outcome is the log
return from that close to the close ``horizon_days`` later. Days whose horizon runs past
the data are left out. The same is measured on every other day, for comparison.

Volatile spells are long, and neighbouring days share most of their next week, so samples
are taken no closer than ``horizon_days`` apart: walking forward through the qualifying
days, a day is used only if it is at least a horizon after the last one used. ``spread``
is the mean return after an up signal minus the mean after a down signal; its confidence
comes from a bootstrap over those non-overlapping samples.

Verdict (pre-registered in the research note), on the volatile days, pooled over markets:
TREND if P(spread > 0) is at least 90% and the spread is larger than ``hurdle`` (one round
trip of costs); REBOUND if P(spread < 0) is at least 90% and the spread is below
``-hurdle``; otherwise KILL. No strategy is built unless the verdict says which way.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime

import polars as pl

from kestrel.data.features import REGIME_VOLATILE
from kestrel.indicators import macd

CONFIDENCE = 0.9


@dataclass(frozen=True)
class Sample:
    market: str
    day: datetime
    up: bool
    """MACD histogram above zero at the day's close."""
    forward: float
    """Log return to the close ``horizon_days`` later."""


def samples(bars: pl.DataFrame, market: str, horizon_days: int, volatile: bool) -> list[Sample]:
    """Non-overlapping samples from the days whose regime is (or isn't) volatile."""
    hist = macd(bars["close"]).histogram.to_list()
    closes = bars["close"].to_list()
    times = bars["open_time"].to_list()
    flags = bars[REGIME_VOLATILE].to_list()
    out: list[Sample] = []
    last_used: int | None = None
    for i, (h, flag) in enumerate(zip(hist, flags, strict=True)):
        if h is None or flag is None or (flag == 1.0) != volatile or h == 0:
            continue
        if i + horizon_days >= len(closes):
            break
        if last_used is not None and i - last_used < horizon_days:
            continue
        out.append(Sample(market, times[i], h > 0, math.log(closes[i + horizon_days] / closes[i])))
        last_used = i
    return out


@dataclass(frozen=True)
class Side:
    n_up: int
    n_down: int
    mean_up: float | None
    mean_down: float | None
    spread: float | None
    p_positive: float | None
    """Bootstrap share of resamples where the spread is above zero."""


def compare(found: list[Sample], resamples: int, seed: int) -> Side:
    ups = [s.forward for s in found if s.up]
    downs = [s.forward for s in found if not s.up]
    if not ups or not downs:
        return Side(len(ups), len(downs), None, None, None, None)
    mean_up, mean_down = sum(ups) / len(ups), sum(downs) / len(downs)
    rng = random.Random(seed)
    above = 0
    for _ in range(resamples):
        u = [rng.choice(ups) for _ in ups]
        d = [rng.choice(downs) for _ in downs]
        above += sum(u) / len(u) > sum(d) / len(d)
    return Side(len(ups), len(downs), mean_up, mean_down, mean_up - mean_down, above / resamples)


@dataclass(frozen=True)
class StudyResult:
    markets: list[str]
    horizon_days: int
    hurdle: float
    volatile: Side
    other: Side

    @property
    def verdict(self) -> str:
        v = self.volatile
        if v.spread is None or v.p_positive is None:
            return "KILL"
        if v.p_positive >= CONFIDENCE and v.spread > self.hurdle:
            return "TREND"
        if 1 - v.p_positive >= CONFIDENCE and v.spread < -self.hurdle:
            return "REBOUND"
        return "KILL"


def run_study(
    markets: dict[str, pl.DataFrame],
    *,
    horizon_days: int = 7,
    hurdle: float,
    resamples: int = 5000,
    seed: int = 0,
) -> StudyResult:
    """``markets``: daily bars with the regime_volatile feature attached, by market."""
    volatile = [s for m, b in markets.items() for s in samples(b, m, horizon_days, True)]
    other = [s for m, b in markets.items() for s in samples(b, m, horizon_days, False)]
    return StudyResult(
        list(markets),
        horizon_days,
        hurdle,
        compare(volatile, resamples, seed),
        compare(other, resamples, seed),
    )


def format_study(r: StudyResult) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{math.expm1(x):+.2%}"

    def row(name: str, s: Side) -> str:
        p = "n/a" if s.p_positive is None else f"{s.p_positive:.0%}"
        return (
            f"  {name:<10} up {s.n_up:>3} weeks {pct(s.mean_up):>8}   down {s.n_down:>3} "
            f"weeks {pct(s.mean_down):>8}   spread {pct(s.spread):>8}   P(spread > 0) {p}"
        )

    verdicts = {
        "TREND": "in volatile spells MACD's direction continued: worth a strategy note.",
        "REBOUND": "in volatile spells moves ran against MACD: rebounds, worth a strategy "
        "note (reversal family: needs its own case).",
        "KILL": "MACD's direction in volatile spells doesn't say which way the next week "
        "goes, by more than costs. No strategy.",
    }
    return "\n".join(
        [
            f"Volatile-spell direction  ·  {', '.join(r.markets)} 1d  ·  next {r.horizon_days} "
            f"days after the MACD 12/26/9 histogram's sign  ·  non-overlapping weeks",
            "",
            row("volatile", r.volatile),
            row("other days", r.other),
            "",
            f"  Hurdle: {math.expm1(r.hurdle):.2%} (one round trip of costs)",
            f"  Verdict: {r.verdict}: {verdicts[r.verdict]}",
        ]
    )
