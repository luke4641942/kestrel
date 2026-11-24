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
- ``stablecoins``: the total USD-pegged stablecoin supply (DefiLlama, kestrel.data.
  stablecoins) known at the close against its value 4 weeks before; on when it grew (new
  dollars entering crypto).

- ``coinbase-premium``: Coinbase's dollar price over Binance's (adjusted by Coinbase's
  USDT-USD), its 7-day mean against the median of the 180 days before; on when above (US
  buying, note does-us-buying-on-coinbase-lead-prices).
- ``variance-premium``: Deribit's DVOL minus 30-day realised volatility, the same 7-day
  against 180-day rule (note does-a-high-variance-premium-lead-prices).
- ``net-liquidity``: Fed assets minus the Treasury's account minus reverse repo, the latest
  Wednesday known at the close (from the Saturday after) against 4 weeks before; on when
  it grew (note does-dollar-liquidity-lead-crypto).

News tone and the last three were pre-registered with an independence control: the
spread must also be positive both in weeks whose past week rose and in weeks whose past
week fell, or the signal is momentum (or dip-buying) under another name and the verdict
is KILL.

Attention rises with price, so the result also reports how often the past week rose when
the signal was on and when it was off: a signal that only repeats last week's move is
momentum under another name (weekly momentum failed out of sample here).
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from kestrel.atomic import write_atomically
from kestrel.data import attention, news, stablecoins
from kestrel.indicators import realised_volatility
from kestrel.studies.volatile_direction import CONFIDENCE, Sample, Side, compare

FEAR_BELOW = 25
SIGNALS = (
    "attention",
    "extreme-fear",
    "news-tone",
    "stablecoins",
    "coinbase-premium",
    "variance-premium",
    "net-liquidity",
)
CONTROLLED = frozenset({"news-tone", "coinbase-premium", "variance-premium", "net-liquidity"})
"""Signals whose notes pre-registered the past-week control."""
RECENT_DAYS = 7
NORMAL_DAYS = 180
LIQUIDITY_KNOWN_AFTER = timedelta(days=3)
"""A Wednesday's H.4.1 figure (published Thursday afternoon US time) counts from Saturday."""


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


def stablecoin_flags(bars: pl.DataFrame, supply: pl.DataFrame) -> list[bool | None]:
    """On when the stablecoin supply known at the close is above its value 4 weeks before."""
    return stablecoins.growing(bars["open_time"].to_list(), supply)


def above_normal(open_times: list[datetime], daily: dict[date, float]) -> list[bool | None]:
    """For each daily bar: is the mean of the last 7 days' values (to the bar's own day) above
    the median of the 180 days before those 7? Unknown if any of the 187 is missing."""
    out: list[bool | None] = []
    for t in open_times:
        day = t.date()
        recent = [daily.get(day - timedelta(days=k)) for k in range(RECENT_DAYS)]
        normal = [
            daily.get(day - timedelta(days=k))
            for k in range(RECENT_DAYS, RECENT_DAYS + NORMAL_DAYS)
        ]
        if any(v is None for v in [*recent, *normal]):
            out.append(None)
            continue
        out.append(
            statistics.fmean(v for v in recent if v is not None)
            > statistics.median(v for v in normal if v is not None)
        )
    return out


def coinbase_premium(
    bars: pl.DataFrame, coinbase: pl.DataFrame, usdt: pl.DataFrame | None
) -> dict[date, float]:
    """Each day's premium: Coinbase's USD close over Binance's USDT close times USDT's dollar
    price, less one (``usdt`` None: unadjusted, USDT taken as a dollar)."""
    cb = dict(zip(coinbase["date"].to_list(), coinbase["value"].to_list(), strict=True))
    tether = (
        None
        if usdt is None
        else dict(zip(usdt["date"].to_list(), usdt["value"].to_list(), strict=True))
    )
    out: dict[date, float] = {}
    for t, close in zip(bars["open_time"].to_list(), bars["close"].to_list(), strict=True):
        day = t.date()
        dollar = 1.0 if tether is None else tether.get(day)
        if day in cb and dollar is not None and close > 0:
            out[day] = cb[day] / (close * dollar) - 1
    return out


def variance_premium(bars: pl.DataFrame, dvol: pl.DataFrame) -> dict[date, float]:
    """Each day's DVOL close less the coin's realised volatility over the last 30 days, both
    annualised percent (365 days)."""
    implied = dict(zip(dvol["date"].to_list(), dvol["value"].to_list(), strict=True))
    realised = realised_volatility(bars, 30).to_list()
    out: dict[date, float] = {}
    for t, rv in zip(bars["open_time"].to_list(), realised, strict=True):
        day = t.date()
        if day in implied and rv is not None and not math.isnan(rv):
            out[day] = implied[day] - 100 * rv
    return out


def net_liquidity(walcl: pl.DataFrame, tga: pl.DataFrame, rrp: pl.DataFrame) -> dict[date, float]:
    """Each Wednesday's net liquidity in billions: WALCL (millions) / 1000 - WTREGEN - RRP."""
    t = dict(zip(tga["date"].to_list(), tga["value"].to_list(), strict=True))
    r = dict(zip(rrp["date"].to_list(), rrp["value"].to_list(), strict=True))
    out: dict[date, float] = {}
    for day, assets in zip(walcl["date"].to_list(), walcl["value"].to_list(), strict=True):
        if day.isoweekday() == 3 and day in t and day in r:
            out[day] = assets / 1000 - t[day] - r[day]
    return out


def liquidity_flags(open_times: list[datetime], weekly: dict[date, float]) -> list[bool | None]:
    """On when the latest Wednesday known at the close is above the Wednesday 4 weeks before.
    Unknown if either is missing (a holiday Wednesday is a gap, not filled)."""
    out: list[bool | None] = []
    for t in open_times:
        close = (t + timedelta(days=1)).date()
        known = close - LIQUIDITY_KNOWN_AFTER
        wednesday = known - timedelta(days=(known.isoweekday() - 3) % 7)
        now, then = weekly.get(wednesday), weekly.get(wednesday - timedelta(weeks=4))
        out.append(None if now is None or then is None else now > then)
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
    after_rise: Side | None = None
    """The control (CONTROLLED signals only): the same spread in weeks whose past week rose."""
    after_fall: Side | None = None

    @property
    def control_holds(self) -> bool | None:
        if self.after_rise is None or self.after_fall is None:
            return None
        return all(
            s.spread is not None and s.spread > 0 for s in (self.after_rise, self.after_fall)
        )

    @property
    def verdict(self) -> str:
        w = self.weeks
        if w.spread is None or w.p_positive is None:
            return "KILL"
        if self.control_holds is False:
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
    weeks = compare(found, resamples, seed)
    if signal not in CONTROLLED:
        return SignalResult(signal, list(markets), hurdle, weeks, overlap)
    rose: list[Sample] = []
    fell: list[Sample] = []
    for m, ss in per_market.items():
        closes = dict(
            zip(markets[m][0]["open_time"].to_list(), markets[m][0]["close"].to_list(), strict=True)
        )
        for s in ss:
            before = closes.get(s.day - timedelta(days=7))
            if before is not None:
                (rose if closes[s.day] > before else fell).append(s)
    return SignalResult(
        signal, list(markets), hurdle, weeks, overlap,
        compare(rose, resamples, seed), compare(fell, resamples, seed),
    )  # fmt: skip


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
        *(
            [
                "",
                "  Control (pre-registered): the spread after a rising and a falling past week",
                f"    after a rise  on {r.after_rise.n_up:>3} / off "
                f"{r.after_rise.n_down:>3} weeks   spread {pct(r.after_rise.spread)}",
                f"    after a fall  on {r.after_fall.n_up:>3} / off "
                f"{r.after_fall.n_down:>3} weeks   spread {pct(r.after_fall.spread)}",
                "    "
                + (
                    "holds: positive in both"
                    if r.control_holds
                    else "FAILS: not positive in both, so it is momentum or dip-buying in disguise"
                ),
            ]
            if r.after_rise is not None and r.after_fall is not None
            else []
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


TITLES = {
    "attention": "Rising attention (Wikipedia)",
    "extreme-fear": "Extreme fear",
    "news-tone": "News tone improving",
    "stablecoins": "Stablecoin supply growing",
    "coinbase-premium": "US buying on Coinbase",
    "variance-premium": "A high variance premium",
    "net-liquidity": "Dollar net liquidity growing",
}


def _side(s: Side) -> dict[str, Any]:
    return {
        "n_on": s.n_up, "n_off": s.n_down, "mean_on": s.mean_up, "mean_off": s.mean_down,
        "spread": s.spread, "p_positive": s.p_positive,
    }  # fmt: skip


def _doc(r: SignalResult) -> dict[str, Any]:
    return {
        "verdict": r.verdict,
        "markets": r.markets,
        "hurdle": r.hurdle,
        "weeks": _side(r.weeks),
        "past_week_rose": {
            m: {"on": o.rose_when_on, "off": o.rose_when_off} for m, o in r.overlap.items()
        },
        "after_rise": None if r.after_rise is None else _side(r.after_rise),
        "after_fall": None if r.after_fall is None else _side(r.after_fall),
        "control_holds": r.control_holds,
    }


def save(
    result: SignalResult,
    note: str,
    report: str,
    data_dir: Path | str,
    commit: str | None,
    beside: SignalResult | None = None,
) -> Path:
    """A frozen note's study for the dashboard (``<data>/signals/<signal>.json``). A rerun of
    the same note replaces it: the study is seeded, so it gives the same numbers unless the
    code changed, and the commit is recorded. ``beside`` is a result its note reports
    alongside, deciding nothing."""
    doc = {
        "signal": result.signal,
        "title": TITLES.get(result.signal, result.signal),
        "note": note,
        "measured_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        **_doc(result),
        "beside": None if beside is None else _doc(beside),
        "report": report,
    }
    target = Path(data_dir) / "signals" / f"{result.signal}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(target, lambda tmp: tmp.write_text(json.dumps(doc, indent=2)))
    return target


def load_all(data_dir: Path | str) -> list[dict[str, Any]]:
    """Every saved signal study, newest first."""
    found = [json.loads(p.read_text()) for p in (Path(data_dir) / "signals").glob("*.json")]
    return sorted(found, key=lambda d: d["measured_at"], reverse=True)
