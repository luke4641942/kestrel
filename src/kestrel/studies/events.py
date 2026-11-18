"""Event studies: what a coin does after an event, against the other coins at the same time.

One shape for every event type (an outside review's suggestion, 2026-11-18): a list of
events (coin, day 0), daily opens for a universe of coins, and

- the **abnormal return** over a window of days after day 0: the coin's open-to-open
  return minus the equal-weighted mean return of every other coin that traded at both
  ends of the same window (returns start at day 1's open: the event is known by day 0's
  close, as in the delisting study);
- a **bootstrap over event days** (coins with the same day 0 are averaged into one);
- a **placebo**: for each event coin, random days far from its event, the same window,
  giving a distribution of mean abnormal returns the real one is compared with (so a coin
  that lags every month isn't mistaken for an effect of the event).

The first event type is a coin's first perpetual futures listing (note
does-a-perp-listing-mark-a-top). Prices come from kestrel.studies.shorting's daily grid.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np
import polars as pl

from kestrel.studies.shorting import Grid, grid

RESAMPLES = 5000


@dataclass(frozen=True)
class Event:
    coin: str
    day: date
    """Day 0: the event is known by this day's close."""


def window_return(g: Grid, j: int, i: int, start: int, end: int) -> float | None:
    """Coin ``j``'s return from the open of day i+start to the open of day i+end, or None
    if either open is missing (or outside the data)."""
    a, b = i + start, i + end
    if a < 0 or b >= len(g.days):
        return None
    lo, hi = g.open[a, j], g.open[b, j]
    if not (np.isfinite(lo) and np.isfinite(hi)) or lo <= 0:
        return None
    return float(hi / lo - 1)


def abnormal(g: Grid, j: int, i: int, start: int, end: int) -> float | None:
    """Coin ``j``'s window return less the equal-weighted mean of every other coin's over
    the same window (those with opens at both ends); None if either is unknown."""
    own = window_return(g, j, i, start, end)
    a, b = i + start, i + end
    if own is None:
        return None
    lo, hi = g.open[a], g.open[b]
    ok = np.isfinite(lo) & np.isfinite(hi) & (lo > 0)
    ok[j] = False
    if not ok.any():
        return None
    return own - float(np.mean(hi[ok] / lo[ok] - 1))


@dataclass
class EventResult:
    events: int
    event_days: int
    dropped: int
    """Events whose primary window couldn't be measured (a missing open)."""
    mean: float
    """Mean over event days of the primary window's abnormal return."""
    p_below: float
    """Bootstrap share of resampled means below zero."""
    share_negative: float
    """Share of events whose abnormal return was below zero."""
    placebo_means: list[float]
    placebo_share_at_or_below: float
    """Share of placebo means at or below the real mean (small: the event matters)."""
    other_windows: dict[str, float] = field(default_factory=dict)
    """Label -> mean over event days, for windows reported but deciding nothing."""
    by_year: dict[int, tuple[int, float]] = field(default_factory=dict)
    """Year -> (events, mean abnormal return)."""
    verdict: str = ""


def by_day(rows: list[tuple[date, float]]) -> np.ndarray:
    """Average events with the same day 0 into one value per day."""
    days: dict[date, list[float]] = {}
    for d, x in rows:
        days.setdefault(d, []).append(x)
    return np.array([float(np.mean(v)) for _, v in sorted(days.items())])


def bootstrap_below(values: np.ndarray, seed: int = 0) -> float:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(RESAMPLES, len(values)))
    return float((values[idx].mean(axis=1) < 0).mean())


def study(
    g: Grid,
    events: Sequence[Event],
    window: tuple[int, int] = (1, 31),
    others: dict[str, tuple[int, int]] | None = None,
    placebo_days: int = 200,
    placebo_gap: int = 60,
    seed: int = 0,
    last_day: date | None = None,
    verdict: Callable[[EventResult], str] | None = None,
) -> EventResult:
    """Measure ``events`` on ``g``. ``last_day``: no window may end after it (the day
    before the holdout). ``verdict`` sets the note's verdict from the result."""
    index = {d: i for i, d in enumerate(g.days)}
    col = {s: j for j, s in enumerate(g.symbols)}
    rows: list[tuple[date, float]] = []
    dropped = 0
    start, end = window
    for e in events:
        x = abnormal(g, col[e.coin], index[e.day], start, end)
        if x is None:
            dropped += 1
        else:
            rows.append((e.day, x))
    values = by_day(rows)
    mean = float(values.mean()) if len(values) else 0.0
    # Placebo: random days per event coin, far from its event, whose window is measurable.
    rng = np.random.default_rng(seed)
    limit = len(g.days) - 1 if last_day is None else index.get(last_day, len(g.days) - 1)
    draws: list[list[float]] = []
    for e in events:
        j, i0 = col[e.coin], index[e.day]
        candidates = [
            i for i in range(len(g.days))
            if abs(i - i0) >= placebo_gap and i + end <= limit and g.age[i, j] > 0
        ]  # fmt: skip
        picks: list[float] = []
        if candidates:
            for i in rng.choice(candidates, size=placebo_days, replace=True):
                x = abnormal(g, j, int(i), start, end)
                picks.append(np.nan if x is None else x)
        draws.append(picks)
    placebo: list[float] = []
    for k in range(placebo_days):
        xs = [d[k] for d in draws if len(d) > k and np.isfinite(d[k])]
        if xs:
            placebo.append(float(np.mean(xs)))
    extra: dict[str, float] = {}
    for label, (a, b) in (others or {}).items():
        measured = [(e.day, abnormal(g, col[e.coin], index[e.day], a, b)) for e in events]
        vals = by_day([(d, x) for d, x in measured if x is not None])
        extra[label] = float(vals.mean()) if len(vals) else float("nan")
    years: dict[int, list[float]] = {}
    for d, x in rows:
        years.setdefault(d.year, []).append(x)
    result = EventResult(
        events=len(rows),
        event_days=len(values),
        dropped=dropped,
        mean=mean,
        p_below=bootstrap_below(values, seed) if len(values) else 0.0,
        share_negative=float(np.mean([x < 0 for _, x in rows])) if rows else 0.0,
        placebo_means=placebo,
        placebo_share_at_or_below=float(np.mean([p <= mean for p in placebo])) if placebo else 1.0,
        other_windows=extra,
        by_year={y: (len(v), float(np.mean(v))) for y, v in sorted(years.items())},
    )
    if verdict is not None:
        result.verdict = verdict(result)
    return result


# ---------- perp listings (note does-a-perp-listing-mark-a-top) ----------

ARCHIVE_START = datetime(2020, 1, 2, tzinfo=UTC)
"""Binance's funding archive starts 2020-01: a perp first seen before this is censored."""
MIN_SPOT_DAYS = 60
PRIMARY = (1, 31)
OTHERS = {"run-up (day -30 to day 1)": (-30, 1), "first week (day 1 to 8)": (1, 8)}


def perp_listings(
    g: Grid, first_funding: dict[str, datetime | None], holdout: datetime
) -> list[Event]:
    """Each coin whose perp first settled funding on or after the archive's start, with
    at least 60 days of unbroken spot history before that day, and its 30-day window
    ending before the holdout."""
    index = {d: i for i, d in enumerate(g.days)}
    out = []
    for coin, first in sorted(first_funding.items()):
        if first is None or first < ARCHIVE_START or coin not in g.symbols:
            continue
        day = first.date()
        i = index.get(day)
        if i is None:
            continue
        j = g.symbols.index(coin)
        if g.age[i, j] < MIN_SPOT_DAYS + 1:
            continue  # the perp came first, or too little spot history before it
        if datetime.combine(day, datetime.min.time(), UTC) + timedelta(days=PRIMARY[1]) >= holdout:
            continue
        out.append(Event(coin, day))
    return out


def perp_verdict(r: EventResult) -> str:
    if r.event_days < 50:
        return "INSUFFICIENT"
    if r.mean <= -0.03 and r.p_below >= 0.90 and r.placebo_share_at_or_below <= 0.10:
        return "EXIT RULE"
    return "KILL"


def run_perp_listing(
    panel: pl.DataFrame,
    first_funding: dict[str, datetime | None],
    holdout: datetime,
    tiers: Sequence[tuple[float, float]],
) -> tuple[EventResult, list[Event]]:
    g = grid(panel, {}, tiers)
    events = perp_listings(g, first_funding, holdout)
    last = (holdout - timedelta(days=1)).date()
    result = study(
        g, events, PRIMARY, OTHERS, last_day=last if last in g.days else None,
        verdict=perp_verdict,
    )  # fmt: skip
    return result, events


def format_result(title: str, r: EventResult) -> str:
    lines = [
        title,
        "",
        f"{r.events} events on {r.event_days} listing days ({r.dropped} not measurable)",
        f"  30-day abnormal return (day 1 to 31, vs the other coins): mean {r.mean:+.2%}; "
        f"P(< 0) {r.p_below:.0%}; {r.share_negative:.0%} of events below zero",
        f"  Placebo (same coins, random days): {r.placebo_share_at_or_below:.0%} of "
        f"{len(r.placebo_means)} placebo means at or below it"
        + (
            f" (placebo median {float(np.median(r.placebo_means)):+.2%})" if r.placebo_means else ""
        ),
    ]
    lines += [f"  {label}: mean {v:+.2%}" for label, v in r.other_windows.items()]
    lines.append(
        "  By year: " + ", ".join(f"{y} {n} at {m:+.1%}" for y, (n, m) in r.by_year.items())
    )
    lines.append(f"\nVerdict: {r.verdict}")
    return "\n".join(lines)
