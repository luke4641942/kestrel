"""Does the dollar fall against other currencies on Fed meeting days? (note
does-the-dollar-fall-against-other-currencies-on; a raw-effect study, not a trial).

The basket: long EUR, GBP, AUD and NZD against USD and short USD against JPY, CHF and CAD,
1/7 of equity each (1x gross). On the daily bar holding a scheduled FOMC statement
(``kestrel.data.fomc``) it buys at the bar's open and sells at its close, each leg paying
its whole spread (buy at the ask, sell at the bid) and one night's financing (rate
difference less the broker's markup, ``kestrel.data.rates.known_rate``), in AUD through
AUDUSD's mids. The same basket on random non-FOMC weekdays is the placebo.

Tests, all needed (the note): (1) the mean net return per FOMC day is above zero with its
95% interval (bootstrap over meetings) above zero; (2) above the 95th percentile of the
random-day sets; (3) above zero over 2014-2022 (after the paper's sample); (4) at least
5 of the 7 legs positive on average. Tests 1 and 2 are run again on daily bars ending
00:00 UTC (``DayEnd``). Never reads the forex holdout.
"""

from __future__ import annotations

import json
import random
import statistics
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from kestrel.atomic import write_atomically
from kestrel.data import fx
from kestrel.data.fomc import statement_instant

LONG = ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD")
"""Long the first currency against USD."""
SHORT = ("USDJPY", "USDCHF", "USDCAD")
"""Short USD against the second currency."""
LEGS = LONG + SHORT
START = datetime(2006, 2, 1, tzinfo=UTC)
LATE_FROM = date(2014, 1, 1)
RESAMPLES = 10_000
PLACEBOS = 1_000
SEED = 0
MIN_LEGS = 5


def currency_of(leg: str) -> str:
    """The non-USD currency a leg holds."""
    return leg[:3] if leg in LONG else leg[3:]


@dataclass(frozen=True)
class Day:
    day: date
    """The statement's date (New York), or the placebo day's bar date."""
    net: float
    gross: float
    legs: dict[str, float]
    """Net return per leg, each as a whole position (the basket is their mean)."""


def leg_return(bar: dict[str, Any], leg: str) -> tuple[float, float]:
    """(net of spread, at mids) for one leg held over one bar: a long leg buys at the ask
    and sells at the bid; a short leg sells at the bid and buys back at the ask."""
    if leg in LONG:
        return bar["bid_close"] / bar["ask_open"] - 1, bar["close"] / bar["open"] - 1
    return bar["bid_open"] / bar["ask_close"] - 1, bar["open"] / bar["close"] - 1


def financing(leg: str, rate: dict[str, float], markup: float) -> float:
    """One night's financing as a share of the leg: holding the foreign currency against
    USD earns (r_foreign - r_usd) and always pays the markup."""
    return (rate[currency_of(leg)] - rate["USD"] - markup) / 365


def basket_day(
    when: date,
    bars: dict[str, dict[str, Any]],
    rate: dict[str, float],
    markup: float,
    aud: dict[str, Any],
) -> Day:
    """The basket held over one bar. P&L in USD is translated to AUD at AUDUSD's mid close
    on an AUD notional set at its mid open, as the forex engine accounts."""
    translate = aud["open"] / aud["close"]
    legs, gross = {}, []
    for leg in LEGS:
        net, mid = leg_return(bars[leg], leg)
        legs[leg] = (net + financing(leg, rate, markup)) * translate
        gross.append(mid * translate)
    return Day(when, statistics.fmean(legs.values()), statistics.fmean(gross), legs)


def bar_holding(frame: pl.DataFrame, instant: datetime) -> dict[str, Any] | None:
    """The daily bar whose span holds ``instant``, or None (a holiday, a missing day)."""
    row = frame.filter(
        (pl.col("open_time") <= instant) & (pl.col("open_time") + timedelta(days=1) > instant)
    )
    return row.row(0, named=True) if row.height == 1 else None


@dataclass
class FomcResult:
    day_end: str
    days: list[Day]
    placebo_means: list[float]
    missing: list[date] = field(default_factory=list)
    """Statement days with no bar on some pair (reported, never filled)."""

    @property
    def mean(self) -> float:
        return statistics.fmean(d.net for d in self.days)

    @property
    def interval(self) -> tuple[float, float]:
        rnd = random.Random(SEED)
        nets = [d.net for d in self.days]
        means = sorted(statistics.fmean(rnd.choices(nets, k=len(nets))) for _ in range(RESAMPLES))
        return means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]

    @property
    def placebo_p95(self) -> float:
        ordered = sorted(self.placebo_means)
        return ordered[int(0.95 * len(ordered)) - 1]

    @property
    def late_mean(self) -> float | None:
        late = [d.net for d in self.days if d.day >= LATE_FROM]
        return statistics.fmean(late) if late else None

    @property
    def leg_means(self) -> dict[str, float]:
        return {leg: statistics.fmean(d.legs[leg] for d in self.days) for leg in LEGS}

    @property
    def test1(self) -> bool:
        return self.interval[0] > 0

    @property
    def test2(self) -> bool:
        return self.mean > self.placebo_p95

    @property
    def test3(self) -> bool:
        return (self.late_mean or 0.0) > 0

    @property
    def test4(self) -> bool:
        return sum(m > 0 for m in self.leg_means.values()) >= MIN_LEGS


def load_bars(
    data_dir: Path | str, day_end: fx.DayEnd, holdout: datetime
) -> dict[str, pl.DataFrame]:
    """Each leg's trading-day bars (and AUDUSD's for translation), before the holdout."""
    out = {}
    for pair in {*LEGS, "AUDUSD"}:
        frame, _ = fx.trading_days(fx.load(data_dir, pair, day_end), day_end)
        out[pair] = frame.filter(
            (pl.col("open_time") >= START - timedelta(days=1)) & (pl.col("open_time") < holdout)
        )
    return out


def run_fomc(
    frames: dict[str, pl.DataFrame],
    statements: list[date],
    rates: Any,
    markup: float,
    day_end: fx.DayEnd,
    holdout: datetime,
    placebos: int = PLACEBOS,
) -> FomcResult:
    """The study on one set of bars. ``rates`` has ``known_rate(currency, at)``."""
    currencies = ["USD", *sorted({currency_of(leg) for leg in LEGS})]
    index = {
        pair: {row["open_time"]: row for row in frame.iter_rows(named=True)}
        for pair, frame in frames.items()
    }

    def day_at(opened: datetime, when: date) -> Day | None:
        bars = {pair: rows.get(opened) for pair, rows in index.items()}
        if any(b is None for b in bars.values()):
            return None
        known = {c: rates.known_rate(c, opened) for c in currencies}
        if any(v is None for v in known.values()):
            return None
        return basket_day(when, bars, known, markup, bars["AUDUSD"])  # type: ignore[arg-type]

    result = FomcResult(day_end, [], [])
    fomc_opens: set[datetime] = set()
    for when in statements:
        instant = statement_instant(when)
        if instant < START or instant >= holdout:
            continue
        bar = bar_holding(frames["EURUSD"], instant)
        day = day_at(bar["open_time"], when) if bar else None
        if day is None:
            result.missing.append(when)
            continue
        fomc_opens.add(bar["open_time"])  # type: ignore[index]
        result.days.append(day)

    # The placebo: random sets of non-FOMC bars, as many as there were FOMC days.
    others = [t for t in frames["EURUSD"]["open_time"].to_list() if t not in fomc_opens]
    others = [t for t in others if START <= t < holdout]
    pool: dict[datetime, Day] = {}
    rnd = random.Random(SEED)
    for _ in range(placebos):
        chosen = rnd.sample(others, len(result.days))
        nets = []
        for t in chosen:
            if t not in pool:
                d = day_at(t, t.date())
                if d is None:
                    continue
                pool[t] = d
            nets.append(pool[t].net)
        result.placebo_means.append(statistics.fmean(nets))
    return result


def verdict(ny: FomcResult, utc: FomcResult) -> str:
    passed = ny.test1 and ny.test2 and ny.test3 and ny.test4 and utc.test1 and utc.test2
    return "PROCEED" if passed else "KILL"


def format_fomc(ny: FomcResult, utc: FomcResult) -> str:
    def bps(x: float | None) -> str:
        return "n/a" if x is None else f"{x * 1e4:+.2f} bps"

    def mark(ok: bool) -> str:
        return "pass" if ok else "FAIL"

    lines = [
        "Fed meeting days  ·  short-dollar basket (EUR, GBP, AUD, NZD, JPY, CHF, CAD vs USD, "
        "1/7 each), held over the statement's daily bar, after spreads and a night's financing",
    ]
    for r, label in ((ny, "Days ending 17:00 New York"), (utc, "Days ending 00:00 UTC")):
        lo, hi = r.interval
        lines += [
            "",
            f"{label}: {len(r.days)} meetings"
            + (
                f" ({len(r.missing)} without a bar: {', '.join(map(str, r.missing))})"
                if r.missing
                else ""
            ),
            f"  1. Mean net per meeting  {bps(r.mean)}, 95% interval [{bps(lo)}, {bps(hi)}]: "
            f"{mark(r.test1)}",
            f"  2. Random days           95th percentile {bps(r.placebo_p95)}: {mark(r.test2)}",
        ]
        if r is ny:
            legs = ", ".join(f"{leg} {bps(m)}" for leg, m in r.leg_means.items())
            positive = sum(m > 0 for m in r.leg_means.values())
            lines += [
                f"  3. 2014-2022             {bps(r.late_mean)}: {mark(r.test3)}",
                f"  4. Legs positive         {positive} of 7 ({MIN_LEGS} needed): {mark(r.test4)}",
                f"     {legs}",
            ]
    lines += ["", f"Verdict: {verdict(ny, utc)}", "", "Reported, deciding nothing:"]
    gross = statistics.fmean(d.gross for d in ny.days)
    lines.append(f"  Before spreads and financing (New York days): {bps(gross)} per meeting")
    by_year: dict[int, list[float]] = {}
    for d in ny.days:
        by_year.setdefault(d.day.year, []).append(d.net)
    lines.append(
        "  By year: "
        + ", ".join(f"{y} {bps(statistics.fmean(v))}" for y, v in sorted(by_year.items()))
    )
    high = statistics.fmean(statistics.fmean([d.legs["AUDUSD"], d.legs["NZDUSD"]]) for d in ny.days)
    low = statistics.fmean(statistics.fmean([d.legs["USDJPY"], d.legs["USDCHF"]]) for d in ny.days)
    lines.append(f"  High-yield legs (AUD, NZD) {bps(high)}; low-yield legs (JPY, CHF) {bps(low)}")
    lines.append(f"  Per year at about 8 meetings: {bps(ny.mean * 8)} of equity")
    return "\n".join(lines)


def save(ny: FomcResult, utc: FomcResult, data_dir: Path | str) -> Path:
    target = Path(data_dir) / "studies" / "fomc-dollar.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "created_at": datetime.now(UTC).isoformat(),
        "verdict": verdict(ny, utc),
        "results": {
            r.day_end: {
                "meetings": len(r.days),
                "missing": [str(d) for d in r.missing],
                "mean": r.mean,
                "interval": r.interval,
                "placebo_p95": r.placebo_p95,
                "late_mean": r.late_mean,
                "leg_means": r.leg_means,
                "tests": [r.test1, r.test2, r.test3, r.test4],
                "days": [asdict(d) for d in r.days],
            }
            for r in (ny, utc)
        },
    }
    text = json.dumps(doc, indent=1, default=str)
    write_atomically(target, lambda tmp: tmp.write_text(text))
    return target
