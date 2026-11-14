"""Do the weeks after a signal beat the weeks without it? Raw-effect studies of signals
that might say when prices are likely to rise, before any strategy is built on them.

Each Sunday's close (UTC) is a decision point; the signal there is on, off, or unknown, and
the outcome is the log return to the close 7 days later. Sundays are a week apart, so the
samples never overlap. The spread is the mean week after the signal was on minus the mean
week after it was off, with its confidence from a bootstrap (kestrel.studies.
volatile_direction.compare). A long-only account can only use a signal that picks better
weeks, so the verdict is PROCEED only if P(spread > 0) is at least 90% and the spread beats
one round trip of costs; otherwise KILL.

Signals, each fixed before it is run:

- ``attention`` (Liu and Tsyvinski): the coin's Wikipedia pageviews over the last 7 known
  days against the average week of the 4 weeks before; on when above. Only counts known by
  the close are used (kestrel.data.attention: a day's count is known two days later).
- ``extreme-fear``: the Crypto Fear & Greed Index below 25 at the close (alternative.me's
  "Extreme Fear"), as known by the close (kestrel.data.sentiment).
- ``news-tone``: the average tone of the coin's news coverage (GDELT, kestrel.data.news)
  over the last 7 known days against the 4 weeks before; on when it is better (coverage
  turning more positive). Tone runs negative overall, so only the change means anything.

Attention rises with price, so the result also reports how often the past week rose when
the signal was on and when it was off: a signal that only repeats last week's move is
momentum under another name (weekly momentum failed out of sample here).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

import polars as pl

from kestrel.data import attention, news
from kestrel.studies.volatile_direction import CONFIDENCE, Sample, Side, compare

FEAR_BELOW = 25
SIGNALS = ("attention", "extreme-fear", "news-tone")


def attention_flags(bars: pl.DataFrame, views: pl.DataFrame) -> list[bool | None]:
    """On when the last 7 known days of pageviews beat the 4 weeks before's average."""
    known = attention.known_at(views)
    by_day = dict(zip(known["date"].to_list(), known["views"].to_list(), strict=True))
    out: list[bool | None] = []
    for t in bars["open_time"].to_list():
        close = t + timedelta(days=1)
        latest = (close - attention.KNOWN_AFTER).date()  # the newest count known at the close
        last7 = [by_day.get(latest - timedelta(days=k)) for k in range(7)]
        prior = [by_day.get(latest - timedelta(days=k)) for k in range(7, 35)]
        if any(v is None or v <= 0 for v in [*last7, *prior]):
            out.append(None)
            continue
        recent = sum(v for v in last7 if v is not None)
        usual = sum(v for v in prior if v is not None) / 4
        out.append(recent > usual)
    return out


def news_flags(bars: pl.DataFrame, tone: pl.DataFrame) -> list[bool | None]:
    """On when the last 7 known days' average news tone beats the 4 weeks before's."""
    by_day = dict(zip(tone["date"].to_list(), tone["tone"].to_list(), strict=True))
    out: list[bool | None] = []
    for t in bars["open_time"].to_list():
        latest = (t + timedelta(days=1) - news.KNOWN_AFTER).date()
        last7 = [by_day.get(latest - timedelta(days=k)) for k in range(7)]
        prior = [by_day.get(latest - timedelta(days=k)) for k in range(7, 35)]
        if any(v is None for v in [*last7, *prior]):
            out.append(None)
            continue
        out.append(
            sum(v for v in last7 if v is not None) / 7 > sum(v for v in prior if v is not None) / 28
        )
    return out


def fear_flags(bars: pl.DataFrame) -> list[bool | None]:
    """On when the Fear & Greed value known at the close is below 25."""
    return [None if v is None else v < FEAR_BELOW for v in bars["fear_greed"].to_list()]


def sunday_samples(
    bars: pl.DataFrame, flags: list[bool | None], market: str, horizon_days: int = 7
) -> list[Sample]:
    closes, times = bars["close"].to_list(), bars["open_time"].to_list()
    out = []
    for i, (t, flag) in enumerate(zip(times, flags, strict=True)):
        if t.isoweekday() != 7 or flag is None or i + horizon_days >= len(closes):
            continue
        if times[i + horizon_days] - t != timedelta(days=horizon_days):
            continue  # a missing bar inside the week: the week isn't measured
        out.append(Sample(market, t, flag, math.log(closes[i + horizon_days] / closes[i])))
    return out


@dataclass(frozen=True)
class Overlap:
    """How often the past week rose, after the signal was on and after it was off."""

    rose_when_on: float | None
    rose_when_off: float | None


def momentum_overlap(bars: pl.DataFrame, found: list[Sample]) -> Overlap:
    closes = dict(zip(bars["open_time"].to_list(), bars["close"].to_list(), strict=True))

    def rose(s: Sample) -> bool | None:
        before = closes.get(s.day - timedelta(days=7))
        return None if before is None else closes[s.day] > before

    def share(on: bool) -> float | None:
        known = [r for s in found if s.up == on and (r := rose(s)) is not None]
        return sum(known) / len(known) if known else None

    return Overlap(share(True), share(False))


@dataclass(frozen=True)
class SignalResult:
    signal: str
    markets: list[str]
    hurdle: float
    weeks: Side
    overlap: dict[str, Overlap]

    @property
    def verdict(self) -> str:
        w = self.weeks
        if w.spread is None or w.p_positive is None:
            return "KILL"
        return "PROCEED" if w.p_positive >= CONFIDENCE and w.spread > self.hurdle else "KILL"


def run_signal_study(
    signal: str,
    markets: dict[str, tuple[pl.DataFrame, list[bool | None]]],
    *,
    hurdle: float,
    resamples: int = 5000,
    seed: int = 0,
) -> SignalResult:
    if signal not in SIGNALS:
        raise ValueError(f"unknown signal {signal!r}; one of {', '.join(SIGNALS)}")
    per_market = {m: sunday_samples(b, f, m) for m, (b, f) in markets.items()}
    found = [s for ss in per_market.values() for s in ss]
    overlap = {m: momentum_overlap(markets[m][0], ss) for m, ss in per_market.items()}
    return SignalResult(signal, list(markets), hurdle, compare(found, resamples, seed), overlap)


def format_signal_study(r: SignalResult) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{math.expm1(x):+.2%}"

    def share(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.0%}"

    w = r.weeks
    p = "n/a" if w.p_positive is None else f"{w.p_positive:.0%}"
    lines = [
        f"Signal study  ·  {r.signal}  ·  {', '.join(r.markets)} 1d  ·  Sunday closes, next 7 days",
        "",
        f"  signal on   {w.n_up:>4} weeks   mean {pct(w.mean_up):>8}",
        f"  signal off  {w.n_down:>4} weeks   mean {pct(w.mean_down):>8}",
        f"  spread {pct(w.spread)}   P(spread > 0) {p}   hurdle {math.expm1(r.hurdle):.2%}",
        "",
        "  Past week rose (is it momentum?):",
        *(
            f"    {m}: {share(o.rose_when_on)} of on-weeks, {share(o.rose_when_off)} of off-weeks"
            for m, o in r.overlap.items()
        ),
        "",
        f"  Verdict: {r.verdict}"
        + (
            ": the weeks after the signal beat the rest by more than costs."
            if r.verdict == "PROCEED"
            else ": the signal doesn't pick better weeks by more than costs. No strategy."
        ),
    ]
    return "\n".join(lines)
