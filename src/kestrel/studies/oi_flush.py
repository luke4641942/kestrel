"""Do falls with forced perp selling bounce more than other falls? (note
do-falls-with-forced-perp-selling-bounce-more, v2). A raw-effect study, run once.

A flush day is a coin's spot fall on a day its perp open interest (contracts) dropped
sharply: the day's log change, z-scored against the coin's previous 90 days (60 known),
below -2. A control fall is a fall with an open-interest z above -0.5 and a size within 25%
of the flush's. Each is bought at the next open and sold at the open five days later, net
of a round trip. A flush's excess is its net return less the mean of its controls'.

Three tests, all needed: the mean excess is above zero with a 95% interval, resampled by
date, that excludes zero (cascades are market-wide, so one date is one observation); the
flushes' own mean net return beats the 95th percentile of the same coins bought on random
days at the same costs; at least 30 distinct flush dates. Nothing is computed across a gap
in either series, and a missing day of open interest is skipped, never filled.
"""

from __future__ import annotations

import bisect
import json
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from kestrel.atomic import write_atomically

FLUSH_Z = -2.0
CONTROL_Z = -0.5
WINDOW = 90
KNOWN = 60
MATCH = 0.25
HOLD = 5
RESAMPLES = 5000
PLACEBOS = 1000
MIN_DATES = 30
SEED = 0
INFO_ONLY = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})
"""Reported apart: their holdout years are spent (the note)."""


@dataclass(frozen=True)
class Day:
    coin: str
    day: date
    ret: float
    """The day's close-to-close spot return."""
    z: float
    net: float
    """Bought at the next open, sold at the open HOLD days later, net of a round trip."""


def coin_days(
    coin: str,
    bars: pl.DataFrame,
    oi: pl.DataFrame,
    fee: float,
    holdout: datetime,
    spreads: list[float] | None = None,
) -> tuple[list[Day], int]:
    """Every scorable day of one coin (z and a forward net return known), and how many days
    had no open interest. ``bars``: spot daily bars (open_time, open, close), sorted;
    ``oi``: daily closes (date, open_interest); ``spreads``: each bar's half-spread
    (fraction), known at its close."""
    times: list[datetime] = bars["open_time"].to_list()
    opens: list[float] = bars["open"].to_list()
    closes: list[float] = bars["close"].to_list()
    half = spreads if spreads is not None else [0.0] * len(times)
    by_day = dict(zip(oi["date"].to_list(), oi["open_interest"].to_list(), strict=True))
    index = {t.date(): i for i, t in enumerate(times)}
    first, last = (times[0].date(), times[-1].date()) if times else (None, None)
    missing = 0
    change: dict[date, float] = {}
    if first is not None and last is not None:
        d = first
        while d <= last:
            now, before = by_day.get(d), by_day.get(d - timedelta(days=1))
            if d in index and now is None:
                missing += 1
            if now and before and now > 0 and before > 0:
                change[d] = math.log(now / before)
            d += timedelta(days=1)
    out: list[Day] = []
    for i, t in enumerate(times):
        d = t.date()
        if d not in change or i == 0 or times[i - 1] != t - timedelta(days=1):
            continue
        past = [change[d - timedelta(days=k)] for k in range(1, WINDOW + 1)
                if d - timedelta(days=k) in change]  # fmt: skip
        if len(past) < KNOWN:
            continue
        sd = statistics.stdev(past)
        if sd == 0:
            continue
        buy, sell = i + 1, i + 1 + HOLD
        if sell >= len(times) or times[sell] - times[i] != timedelta(days=HOLD + 1):
            continue  # a gap, or the end of the data
        if times[sell] >= holdout:
            continue
        cost = 2 * (fee + half[i])
        net = opens[sell] / opens[buy] * (1 - cost) - 1
        z = (change[d] - statistics.fmean(past)) / sd
        out.append(Day(coin, d, closes[i] / closes[i - 1] - 1, z, net))
    return out, missing


@dataclass(frozen=True)
class Flush:
    day: Day
    excess: float
    controls: int


@dataclass
class OIFlushResult:
    flushes: list[Flush]
    unmatched: int
    excess: float | None
    interval: tuple[float, float] | None
    flush_net: float | None
    placebo_p95: float | None
    dates: int
    info: list[Flush] = field(default_factory=list)
    missing_days: int = 0

    @property
    def test1(self) -> bool:
        return self.excess is not None and self.interval is not None and self.interval[0] > 0

    @property
    def test2(self) -> bool:
        return (
            self.flush_net is not None
            and self.placebo_p95 is not None
            and self.flush_net > self.placebo_p95
        )

    @property
    def test3(self) -> bool:
        return self.dates >= MIN_DATES

    @property
    def verdict(self) -> str:
        return "PROCEED" if self.test1 and self.test2 and self.test3 else "KILL"


def match(flushes: list[Day], pool: list[Day]) -> tuple[list[Flush], int]:
    """Each flush against the control falls within MATCH of its own fall (any coin, date)."""
    controls = sorted((d for d in pool if d.ret < 0 and d.z > CONTROL_Z), key=lambda d: d.ret)
    rets = [c.ret for c in controls]
    out: list[Flush] = []
    unmatched = 0
    for f in flushes:
        lo, hi = f.ret * (1 + MATCH), f.ret * (1 - MATCH)  # f.ret < 0: lo is the deeper fall
        a, b = bisect.bisect_left(rets, lo), bisect.bisect_right(rets, hi)
        found = controls[a:b]
        if not found:
            unmatched += 1
            continue
        out.append(Flush(f, f.net - statistics.fmean(c.net for c in found), len(found)))
    return out, unmatched


def date_bootstrap(
    flushes: list[Flush], resamples: int = RESAMPLES, seed: int = SEED
) -> tuple[float, float] | None:
    """The 95% interval of the mean excess, resampling dates (all of a date's flushes go
    together)."""
    by_date: dict[date, list[float]] = defaultdict(list)
    for f in flushes:
        by_date[f.day.day].append(f.excess)
    groups = list(by_date.values())
    if len(groups) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        drawn = [x for _ in groups for x in rng.choice(groups)]
        means.append(statistics.fmean(drawn))
    means.sort()
    return means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]


def placebo_p95(
    flushes: list[Flush],
    days_by_coin: dict[str, list[Day]],
    sets: int = PLACEBOS,
    seed: int = SEED,
) -> float | None:
    """The 95th percentile of the mean net return of the same coins bought on random days."""
    if not flushes:
        return None
    rng = random.Random(seed)
    coins = [f.day.coin for f in flushes]
    means = []
    for _ in range(sets):
        means.append(statistics.fmean(rng.choice(days_by_coin[c]).net for c in coins))
    means.sort()
    return means[int(0.95 * sets)]


def run_oi_flush(days_by_coin: dict[str, list[Day]], missing_days: int = 0) -> OIFlushResult:
    verdict_coins = {c: d for c, d in days_by_coin.items() if c not in INFO_ONLY}
    pool = [d for ds in verdict_coins.values() for d in ds]
    flushes, unmatched = match([d for d in pool if d.ret < 0 and d.z < FLUSH_Z], pool)
    info_pool = [d for c, ds in days_by_coin.items() if c in INFO_ONLY for d in ds]
    info, _ = match([d for d in info_pool if d.ret < 0 and d.z < FLUSH_Z], pool + info_pool)
    return OIFlushResult(
        flushes=flushes,
        unmatched=unmatched,
        excess=statistics.fmean(f.excess for f in flushes) if flushes else None,
        interval=date_bootstrap(flushes),
        flush_net=statistics.fmean(f.day.net for f in flushes) if flushes else None,
        placebo_p95=placebo_p95(flushes, verdict_coins),
        dates=len({f.day.day for f in flushes}),
        info=info,
        missing_days=missing_days,
    )


def format_oi_flush(
    r: OIFlushResult,
    coins: int,
    market_down: set[date] | None = None,
    trend_cash: dict[tuple[str, date], bool] | None = None,
) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:+.2%}"

    ci = "n/a" if r.interval is None else f"[{r.interval[0]:+.2%}, {r.interval[1]:+.2%}]"
    mark = {True: "pass", False: "FAIL"}
    lines = [
        f"Forced selling  ·  {coins} coins (BTC, ETH, SOL apart)  ·  falls with an "
        "open-interest flush vs matched falls, 5 days from the next open, after costs",
        "",
        f"  Flushes          {len(r.flushes)} on {r.dates} dates "
        f"({r.unmatched} without a matched control left out)",
        f"  1. Excess        {pct(r.excess)} a flush over matched falls, 95% interval by date "
        f"{ci}: {mark[r.test1]}",
        f"  2. Random days   flushes {pct(r.flush_net)} net against the 95th percentile of the "
        f"same coins on random days {pct(r.placebo_p95)}: {mark[r.test2]}",
        f"  3. Dates         {r.dates} distinct dates ({MIN_DATES} needed): {mark[r.test3]}",
        "",
        f"  Verdict: {r.verdict}"
        + (
            ": falls with forced selling bounce more than other falls, by more than costs."
            if r.verdict == "PROCEED"
            else (
                ": flush and ordinary falls bounce alike: this is dip-buying under another "
                "name, and it dies with that family."
                if not r.test1
                else ": not enough to proceed."
            )
        ),
        "",
        "  Reported, deciding nothing:",
        f"    BTC, ETH, SOL: {len(r.info)} flushes, excess "
        f"{pct(statistics.fmean(f.excess for f in r.info) if r.info else None)}",
    ]
    by_year: dict[int, list[float]] = defaultdict(list)
    for f in r.flushes:
        by_year[f.day.day.year].append(f.excess)
    for year in sorted(by_year):
        xs = by_year[year]
        lines.append(f"    {year}: {len(xs)} flushes, excess {pct(statistics.fmean(xs))}")
    if market_down is not None and r.flushes:
        wide = [f.excess for f in r.flushes if f.day.day in market_down]
        alone = [f.excess for f in r.flushes if f.day.day not in market_down]
        lines.append(
            f"    With BTC down too: {len(wide)}, excess "
            f"{pct(statistics.fmean(wide) if wide else None)}; coin alone: {len(alone)}, "
            f"excess {pct(statistics.fmean(alone) if alone else None)}"
        )
    if trend_cash is not None and r.flushes:
        known = [trend_cash[(f.day.coin, f.day.day)] for f in r.flushes
                 if (f.day.coin, f.day.day) in trend_cash]  # fmt: skip
        if known:
            lines.append(
                f"    trend-vol-target on the same coin was in cash on "
                f"{sum(known) / len(known):.0%} of flush days (the higher, the more this is a "
                "second bet rather than more of the trend one)"
            )
    lines.append(f"    Coin-days with no open interest (skipped, not filled): {r.missing_days}")
    return "\n".join(lines)


def save(
    r: OIFlushResult, slug: str, report: str, data_dir: Path | str, commit: str | None
) -> Path:
    """The study's result (``<data>/studies/oi-flush.json``); a rerun of the same frozen
    note replaces it."""
    doc = {
        "name": "oi-flush",
        "note": slug,
        "measured_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "verdict": r.verdict,
        "flushes": len(r.flushes),
        "dates": r.dates,
        "unmatched": r.unmatched,
        "excess": r.excess,
        "interval": list(r.interval) if r.interval else None,
        "flush_net": r.flush_net,
        "placebo_p95": r.placebo_p95,
        "tests": {"excess": r.test1, "random_days": r.test2, "dates": r.test3},
        "report": report,
    }
    target = Path(data_dir) / "studies" / "oi-flush.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(target, lambda tmp: tmp.write_text(json.dumps(doc, indent=2)))
    return target
