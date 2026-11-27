"""Do market-wide liquidation flushes bounce, on coins the forced-selling study never used?
(note do-market-wide-liquidation-flushes-bounce-on). A raw-effect study, run once.

Every measurement is findings 32's (``kestrel.studies.oi_flush``): flush days, matched
control falls from any coin and date in this set, a 5-day hold from the next open after
costs, the excess over controls. The coins are the perps listed after 2021-12-01 and by
2024-10-31. A flush is market-wide when BTC's spot close fell the same day.

Four tests, all needed: (1) market-wide flushes' mean excess is above zero with a 95%
interval by date that excludes zero; (2) it is above coin-alone flushes' mean excess;
(3) market-wide flushes' own mean net beats the 95th percentile of the same coins on
random days; (4) at least 30 distinct market-wide flush dates.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from kestrel.atomic import write_atomically
from kestrel.studies.oi_flush import (
    FLUSH_Z,
    MIN_DATES,
    Day,
    Flush,
    date_bootstrap,
    match,
    placebo_p95,
)


@dataclass
class MarketResult:
    wide: list[Flush]
    alone: list[Flush]
    unmatched: int
    interval: tuple[float, float] | None
    alone_interval: tuple[float, float] | None
    placebo_p95: float | None
    missing_days: int = 0

    @staticmethod
    def _mean(xs: list[float]) -> float | None:
        return statistics.fmean(xs) if xs else None

    @property
    def wide_excess(self) -> float | None:
        return self._mean([f.excess for f in self.wide])

    @property
    def alone_excess(self) -> float | None:
        return self._mean([f.excess for f in self.alone])

    @property
    def wide_net(self) -> float | None:
        return self._mean([f.day.net for f in self.wide])

    @property
    def dates(self) -> int:
        return len({f.day.day for f in self.wide})

    @property
    def test1(self) -> bool:
        return self.interval is not None and self.interval[0] > 0

    @property
    def test2(self) -> bool:
        w, a = self.wide_excess, self.alone_excess
        return w is not None and a is not None and w > a

    @property
    def test3(self) -> bool:
        n, p = self.wide_net, self.placebo_p95
        return n is not None and p is not None and n > p

    @property
    def test4(self) -> bool:
        return self.dates >= MIN_DATES

    @property
    def verdict(self) -> str:
        return "PROCEED" if self.test1 and self.test2 and self.test3 and self.test4 else "KILL"


def run_market(
    days_by_coin: dict[str, list[Day]], market_down: set[date], missing_days: int = 0
) -> MarketResult:
    pool = [d for ds in days_by_coin.values() for d in ds]
    flushes, unmatched = match([d for d in pool if d.ret < 0 and d.z < FLUSH_Z], pool)
    wide = [f for f in flushes if f.day.day in market_down]
    alone = [f for f in flushes if f.day.day not in market_down]
    return MarketResult(
        wide=wide,
        alone=alone,
        unmatched=unmatched,
        interval=date_bootstrap(wide),
        alone_interval=date_bootstrap(alone),
        placebo_p95=placebo_p95(wide, days_by_coin),
        missing_days=missing_days,
    )


def format_market(
    r: MarketResult,
    coins: int,
    earlier_dates: set[date] | None = None,
    trend_cash: dict[tuple[str, date], bool] | None = None,
) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:+.2%}"

    def ci(x: tuple[float, float] | None) -> str:
        return "n/a" if x is None else f"[{x[0]:+.2%}, {x[1]:+.2%}]"

    mark = {True: "pass", False: "FAIL"}
    lines = [
        f"Market-wide flushes  ·  {coins} coins never in findings 32 (perps listed 2021-12 to "
        "2024-10)  ·  5 days from the next open, after costs",
        "",
        f"  Market-wide flushes (BTC down too) {len(r.wide)} on {r.dates} dates; coin alone "
        f"{len(r.alone)}; {r.unmatched} without a matched control left out",
        f"  1. Excess        {pct(r.wide_excess)} a flush over matched falls, 95% interval by "
        f"date {ci(r.interval)}: {mark[r.test1]}",
        f"  2. vs coin alone {pct(r.wide_excess)} against {pct(r.alone_excess)}: {mark[r.test2]}",
        f"  3. Random days   {pct(r.wide_net)} net against the 95th percentile of the same "
        f"coins on random days {pct(r.placebo_p95)}: {mark[r.test3]}",
        f"  4. Dates         {r.dates} distinct dates ({MIN_DATES} needed): {mark[r.test4]}",
        "",
        f"  Verdict: {r.verdict}",
        "",
        "  Reported, deciding nothing:",
        f"    Coin-alone flushes: excess {pct(r.alone_excess)}, 95% interval by date "
        f"{ci(r.alone_interval)}",
    ]
    by_year: dict[int, list[float]] = defaultdict(list)
    for f in r.wide:
        by_year[f.day.day.year].append(f.excess)
    for year in sorted(by_year):
        xs = by_year[year]
        lines.append(
            f"    {year}: {len(xs)} market-wide flushes, excess {pct(statistics.fmean(xs))}"
        )
    if earlier_dates is not None and r.wide:
        shared = {f.day.day for f in r.wide} & earlier_dates
        lines.append(
            f"    Market-wide flush dates that were also flush dates in findings 32: "
            f"{len(shared)} of {r.dates} (the same market falls, on other coins)"
        )
    if trend_cash is not None and r.wide:
        known = [trend_cash[(f.day.coin, f.day.day)] for f in r.wide
                 if (f.day.coin, f.day.day) in trend_cash]  # fmt: skip
        if known:
            lines.append(
                f"    trend-vol-target on the same coin was in cash on "
                f"{sum(known) / len(known):.0%} of market-wide flush days"
            )
    lines.append(f"    Coin-days with no open interest (skipped, not filled): {r.missing_days}")
    return "\n".join(lines)


def save(r: MarketResult, slug: str, report: str, data_dir: Path | str, commit: str | None) -> Path:
    doc = {
        "name": "oi-flush-market",
        "note": slug,
        "measured_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "verdict": r.verdict,
        "market_wide": len(r.wide),
        "coin_alone": len(r.alone),
        "dates": r.dates,
        "excess": r.wide_excess,
        "interval": list(r.interval) if r.interval else None,
        "alone_excess": r.alone_excess,
        "wide_net": r.wide_net,
        "placebo_p95": r.placebo_p95,
        "tests": [r.test1, r.test2, r.test3, r.test4],
        "report": report,
    }
    target = Path(data_dir) / "studies" / "oi-flush-market.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(target, lambda tmp: tmp.write_text(json.dumps(doc, indent=2)))
    return target
