"""Is Monday's range special? A raw-effect study of the "Monday range" idea.

The claim (S1 in the strategy backlog): Monday's high and low in Brisbane time (AEST,
UTC+10, no daylight saving) set the week's direction: whichever side breaks first tends
to continue, and in weeks that break up first, a pullback to Monday's midpoint is a good
buy.

A share of "up-first" weeks means nothing on its own: BTC rose about fifteen-fold over
the research period, so almost any range breaks up first more often than not. The fair
question is whether Monday is different from other days. So every calendar day (AEST) is
used as an anchor, measured identically:

- anchor range: the high and low of the anchor day's bars; ``mid`` is their midpoint;
- horizon: the next ``horizon_days`` full days (default 4: for a Monday, Tuesday to
  Friday, matching the strategy's Friday exit), the same length for every weekday;
- first break: the first horizon bar to close above the high ("up") or below the low
  ("down"), or neither;
- continuation: the return from the breaking bar's close to the horizon's last close;
- pullback: in up-first cases, the first later bar whose low comes within ``tolerance``
  of the midpoint; buy at the next bar's open and sell at the horizon's last close.

Monday is compared with all other weekdays pooled. Confidence intervals come from a
bootstrap that resamples whole weeks, because neighbouring anchor days share price paths.

Verdict (pre-registered in the research note): Monday's effect is real only if, with 95%
confidence, its up-first share is above the other days', its pullback return is above
theirs, and its average pullback return clears ``hurdle`` (one round trip of costs).
Otherwise the idea is killed before any strategy is built on it.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import polars as pl

AEST = timedelta(hours=10)
"""Brisbane: UTC+10 all year."""
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
RESAMPLES = 2000
SEED = 20261111


@dataclass(frozen=True)
class AnchorOutcome:
    day: date
    """The anchor day, in AEST."""
    week: date
    """Monday of the anchor's week (AEST): the unit the bootstrap resamples."""
    weekday: int
    """0 = Monday."""
    first: str
    """"up", "down" or "none"."""
    continuation: float | None
    """Return from the breaking close to the horizon's end."""
    pullback: float | None
    """Return of buying the pullback to the midpoint (up-first cases that had one)."""


@dataclass
class WeekdayStats:
    weekday: str
    days: int
    broken: int
    up_first: float | None
    continuation_up: float | None
    continuation_down: float | None
    pullbacks: int
    pullback_return: float | None


@dataclass
class StudyResult:
    symbol: str
    start: datetime
    end: datetime
    horizon_days: int
    tolerance: float
    hurdle: float
    rows: list[WeekdayStats]
    up_first_diff_ci: tuple[float, float]
    """Monday's up-first share minus the other days', 95% interval."""
    pullback_diff_ci: tuple[float, float]
    """Monday's average pullback return minus the other days', 95% interval."""
    monday_pullback_ci: tuple[float, float]
    outcomes: list[AnchorOutcome] = field(repr=False, default_factory=list)

    @property
    def survives(self) -> bool:
        return (
            self.up_first_diff_ci[0] > 0
            and self.pullback_diff_ci[0] > 0
            and self.monday_pullback_ci[0] > self.hurdle
        )


def anchor_outcomes(
    bars: pl.DataFrame, horizon_days: int = 4, tolerance: float = 0.005
) -> list[AnchorOutcome]:
    """Measure every complete anchor day that has a complete horizon after it."""
    step = bars["open_time"].diff().median()
    if not isinstance(step, timedelta) or step > timedelta(hours=1):
        raise ValueError("the study needs bars of 1h or shorter, so AEST days align with bars")
    per_day = round(timedelta(days=1) / step)
    local = bars.with_columns(day=(pl.col("open_time") + AEST).dt.date())
    days: dict[date, pl.DataFrame] = {
        key[0]: frame
        for key, frame in local.partition_by("day", as_dict=True, maintain_order=True).items()
    }
    outcomes = []
    for day, anchor in days.items():
        horizon_days_frames = [
            days.get(day + timedelta(days=k)) for k in range(1, horizon_days + 1)
        ]
        if anchor.height != per_day or any(
            f is None or f.height != per_day for f in horizon_days_frames
        ):
            continue  # an incomplete day would distort its range or its outcome
        horizon = pl.concat([f for f in horizon_days_frames if f is not None])
        outcomes.append(_measure(day, anchor, horizon, tolerance))
    return outcomes


def _measure(day: date, anchor: pl.DataFrame, horizon: pl.DataFrame, tol: float) -> AnchorOutcome:
    high, low = float(anchor["high"].max()), float(anchor["low"].min())  # type: ignore[arg-type]
    mid = (high + low) / 2
    opens, lows, closes = (
        horizon["open"].to_list(),
        horizon["low"].to_list(),
        horizon["close"].to_list(),
    )
    last = closes[-1]
    first, at = "none", -1
    for i, c in enumerate(closes):
        if c > high:
            first, at = "up", i
            break
        if c < low:
            first, at = "down", i
            break
    continuation = last / closes[at] - 1 if at >= 0 else None
    pullback = None
    if first == "up":
        for j in range(at + 1, len(closes) - 1):
            if lows[j] <= mid * (1 + tol):
                pullback = last / opens[j + 1] - 1
                break
    week = day - timedelta(days=day.weekday())
    return AnchorOutcome(day, week, day.weekday(), first, continuation, pullback)


def run_study(
    bars: pl.DataFrame,
    symbol: str,
    *,
    horizon_days: int = 4,
    tolerance: float = 0.005,
    hurdle: float = 0.0022,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> StudyResult:
    outcomes = anchor_outcomes(bars, horizon_days, tolerance)
    if not any(o.weekday == 0 for o in outcomes):
        raise ValueError("no complete Mondays with a complete horizon in this range")
    rows = [_stats(WEEKDAYS[d], [o for o in outcomes if o.weekday == d]) for d in range(7)]

    by_week: dict[date, list[AnchorOutcome]] = {}
    for o in outcomes:
        by_week.setdefault(o.week, []).append(o)
    weeks = list(by_week)
    rng = random.Random(seed)
    up_diffs, pb_diffs, mon_pb = [], [], []
    for _ in range(resamples):
        sample = [o for _ in weeks for o in by_week[rng.choice(weeks)]]
        mon = [o for o in sample if o.weekday == 0]
        other = [o for o in sample if o.weekday != 0]
        a, b = _up_share(mon), _up_share(other)
        if a is not None and b is not None:
            up_diffs.append(a - b)
        pa, pb = _mean_pullback(mon), _mean_pullback(other)
        if pa is not None and pb is not None:
            pb_diffs.append(pa - pb)
        if pa is not None:
            mon_pb.append(pa)
    return StudyResult(
        symbol=symbol,
        start=bars["open_time"][0],
        end=bars["open_time"][-1],
        horizon_days=horizon_days,
        tolerance=tolerance,
        hurdle=hurdle,
        rows=rows,
        up_first_diff_ci=_ci(up_diffs),
        pullback_diff_ci=_ci(pb_diffs),
        monday_pullback_ci=_ci(mon_pb),
        outcomes=outcomes,
    )


def _up_share(outcomes: list[AnchorOutcome]) -> float | None:
    broken = [o for o in outcomes if o.first != "none"]
    return sum(o.first == "up" for o in broken) / len(broken) if broken else None


def _mean_pullback(outcomes: list[AnchorOutcome]) -> float | None:
    rets = [o.pullback for o in outcomes if o.pullback is not None]
    return sum(rets) / len(rets) if rets else None


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _stats(name: str, outcomes: list[AnchorOutcome]) -> WeekdayStats:
    return WeekdayStats(
        weekday=name,
        days=len(outcomes),
        broken=sum(o.first != "none" for o in outcomes),
        up_first=_up_share(outcomes),
        continuation_up=_mean(
            [o.continuation for o in outcomes if o.first == "up" and o.continuation is not None]
        ),
        continuation_down=_mean(
            [o.continuation for o in outcomes if o.first == "down" and o.continuation is not None]
        ),
        pullbacks=sum(o.pullback is not None for o in outcomes),
        pullback_return=_mean_pullback(outcomes),
    )


def _ci(values: list[float]) -> tuple[float, float]:
    if not values:
        return (math.nan, math.nan)
    s = sorted(values)
    return s[int(0.025 * len(s))], s[min(len(s) - 1, int(0.975 * len(s)))]


def format_study(r: StudyResult) -> str:
    def pct(x: float | None, digits: int = 2) -> str:
        return "n/a" if x is None or math.isnan(x) else f"{x:+.{digits}%}"

    def share(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.1%}"

    lines = [
        f"Anchor-day range study  ·  {r.symbol}  ·  {r.start:%Y-%m-%d} to {r.end:%Y-%m-%d}",
        f"Days in AEST (UTC+10). Horizon {r.horizon_days} days. Pullback within "
        f"{r.tolerance:.2%} of the midpoint, bought at the next open, sold at the horizon's end.",
        "",
        f"  {'Anchor':<10} {'days':>5} {'broken':>7} {'up first':>9} {'after up':>9} "
        f"{'after down':>11} {'pullbacks':>10} {'pullback ret':>13}",
    ]
    for row in r.rows:
        lines.append(
            f"  {row.weekday:<10} {row.days:>5} {row.broken:>7} {share(row.up_first):>9} "
            f"{pct(row.continuation_up):>9} {pct(row.continuation_down):>11} "
            f"{row.pullbacks:>10} {pct(row.pullback_return):>13}"
        )
    lo, hi = r.up_first_diff_ci
    plo, phi = r.pullback_diff_ci
    mlo, mhi = r.monday_pullback_ci
    lines += [
        "",
        "  Monday against the other days (95% intervals, resampling whole weeks):",
        f"    up-first share, Monday minus others   {pct(lo, 1)} to {pct(hi, 1)}",
        f"    pullback return, Monday minus others  {pct(plo)} to {pct(phi)}",
        f"    Monday pullback return                {pct(mlo)} to {pct(mhi)}"
        f"   (hurdle {r.hurdle:.2%}, one round trip of costs)",
        "",
        "  Verdict: "
        + (
            "SURVIVES. Monday is different, and its pullback clears costs."
            if r.survives
            else "KILL. Monday is not distinguishable from other days, or its pullback "
            "doesn't clear costs."
        ),
    ]
    return "\n".join(lines)
