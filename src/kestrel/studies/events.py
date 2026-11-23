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

import json
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from kestrel.atomic import write_atomically
from kestrel.studies.shorting import Grid, grid

RESAMPLES = 5000


@dataclass(frozen=True)
class Event:
    coin: str
    day: date
    """Day 0: the event is known by this day's close."""


def window_return(g: Grid, j: int, i: int, start: int, end: int) -> float | None:
    """Coin ``j``'s return from the open of day i+start to the open of day i+end, or None
    if either open is missing (or outside the data) or the window crosses a break in the
    coin's history (a relisting or a token swap: nothing is computed across a gap)."""
    a, b = i + start, i + end
    if a < 0 or b >= len(g.days):
        return None
    lo, hi = g.open[a, j], g.open[b, j]
    if not (np.isfinite(lo) and np.isfinite(hi)) or lo <= 0:
        return None
    if g.age[b, j] - g.age[a, j] != b - a:
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
    unbroken = (g.age[b] - g.age[a]) == (b - a)
    ok = np.isfinite(lo) & np.isfinite(hi) & (lo > 0) & unbroken
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
    avoid: dict[str, list[date]] | None = None,
) -> EventResult:
    """Measure ``events`` on ``g``. ``last_day``: no window may end after it (the day
    before the holdout). ``verdict`` sets the note's verdict from the result. ``avoid``:
    per coin, more days placebo draws must stay ``placebo_gap`` days from (its own event
    day always is)."""
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
        far = [i0] + [index[d] for d in (avoid or {}).get(e.coin, []) if d in index]
        candidates = [
            i for i in range(len(g.days))
            if all(abs(i - k) >= placebo_gap for k in far) and i + end <= limit
            and g.age[i, j] > 0
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
) -> tuple[EventResult, list[Event], list[tuple[int, float]]]:
    g = grid(panel, {}, tiers)
    events = perp_listings(g, first_funding, holdout)
    last = (holdout - timedelta(days=1)).date()
    result = study(
        g, events, PRIMARY, OTHERS, last_day=last if last in g.days else None,
        verdict=perp_verdict,
    )  # fmt: skip
    return result, events, event_path(g, events)


# ---------- Coinbase listings (note does-a-coinbase-listing-move-a-coin-on-binance) ----------


def open_universe(stored: Collection[str], locked: Collection[str]) -> list[str]:
    """Every stored USDT pair that is a coin (kestrel.data.universe: no leveraged tokens,
    stablecoins, fiat, gold or tokenized stocks), less the locked confirmation half: the
    exploration half plus the coins too young to have been split. The Coinbase-listing
    study's universe (its note, v1, set after counting events and before any return)."""
    from kestrel.data.universe import split_universe

    coins, _ = split_universe(sorted(stored))
    return sorted(set(coins) - set(locked))


def coinbase_listings(g: Grid, first_day: dict[str, date], holdout: datetime) -> list[Event]:
    """Each coin's first trading day on Coinbase, where the coin already had at least 60
    days of unbroken Binance spot history (Binance first) and its 30-day window ends before
    the holdout."""
    index = {d: i for i, d in enumerate(g.days)}
    out = []
    for coin, day in sorted(first_day.items()):
        i = index.get(day)
        if i is None or coin not in g.symbols:
            continue
        if g.age[i, g.symbols.index(coin)] < MIN_SPOT_DAYS + 1:
            continue  # Coinbase listed it first, or too little Binance history before
        if datetime.combine(day, datetime.min.time(), UTC) + timedelta(days=PRIMARY[1]) >= holdout:
            continue
        out.append(Event(coin, day))
    return out


def coinbase_verdict(r: EventResult) -> str:
    """Two-sided (either sign would be news), so each side needs 95%, not 90%."""
    if r.event_days < 50:
        return "INSUFFICIENT"
    p_above = 1 - r.p_below
    placebo_at_or_above = 1 - r.placebo_share_at_or_below
    if r.mean >= 0.03 and p_above >= 0.95 and placebo_at_or_above <= 0.05:
        return "LEAD"
    if r.mean <= -0.03 and r.p_below >= 0.95 and r.placebo_share_at_or_below <= 0.05:
        return "EXIT RULE"
    return "KILL"


def run_coinbase_listing(
    panel: pl.DataFrame,
    first_day: dict[str, date],
    holdout: datetime,
    tiers: Sequence[tuple[float, float]],
) -> tuple[EventResult, list[Event], list[tuple[int, float]]]:
    g = grid(panel, {}, tiers)
    found = coinbase_listings(g, first_day, holdout)
    last = (holdout - timedelta(days=1)).date()
    result = study(
        g, found, PRIMARY, OTHERS, last_day=last if last in g.days else None,
        verdict=coinbase_verdict,
    )  # fmt: skip
    return result, found, event_path(g, found)


def format_result(title: str, r: EventResult, primary: str = "day 1 to 31") -> str:
    lines = [
        title,
        "",
        f"{r.events} events on {r.event_days} listing days ({r.dropped} not measurable)",
        f"  30-day abnormal return ({primary}, vs the other coins): mean {r.mean:+.2%}; "
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


# ---------- token unlocks (note do-coins-fall-into-large-token-unlocks) ----------

UNLOCK_SHARE = 0.02
"""An event: tokens unlocked that day are at least 2% of the cumulative unlocked supply."""
MERGE_DAYS = 30
UNLOCK_PRIMARY = (-30, 0)
UNLOCK_OTHERS = {"after (day 0 to 14)": (0, 14), "last two weeks (day -14 to 0)": (-14, 0)}


@dataclass(frozen=True)
class Unlock:
    coin: str
    day: date
    tokens: float
    insider_tokens: float


def unlock_days(schedule: pl.DataFrame, share: float = UNLOCK_SHARE) -> list[Unlock]:
    """A protocol's large unlock days (``coin`` left empty): tokens unlocked that day at
    least ``share`` of the cumulative unlocked supply the day before (which must be
    above zero: a token's first day is its launch, not an unlock)."""
    s = schedule.sort("day").with_columns(
        prev=pl.col("unlocked").shift(1), prev_insiders=pl.col("insiders").shift(1)
    )
    big = s.filter(
        (pl.col("prev") > 0) & ((pl.col("unlocked") - pl.col("prev")) >= share * pl.col("prev"))
    )
    return [
        Unlock("", d, u - p, i - pi)
        for d, u, p, i, pi in big.select(
            "day", "unlocked", "prev", "insiders", "prev_insiders"
        ).iter_rows()
    ]


def unlock_events(
    g: Grid,
    schedules: pl.DataFrame,
    mapping: dict[str, str],
    holdout: datetime,
    first: date = date(2020, 1, 1),
) -> tuple[list[Unlock], dict[str, list[date]]]:
    """The note's events: each mapped coin of ``g``'s universe, its large unlock days from
    ``first`` to before the holdout, merged within 30 days (the first kept), with 60 days
    of unbroken spot history before day -30. Also every large unlock day per coin (before
    merging), which placebo draws stay away from."""
    index = {d: i for i, d in enumerate(g.days)}
    events: list[Unlock] = []
    every: dict[str, list[date]] = {}
    for protocol, coin in sorted(mapping.items()):
        if coin not in g.symbols:
            continue
        j = g.symbols.index(coin)
        days = unlock_days(schedules.filter(pl.col("protocol") == protocol))
        every[coin] = [u.day for u in days]
        last: date | None = None
        for u in days:
            if u.day < first or u.day >= holdout.date():
                continue
            if last is not None and (u.day - last).days <= MERGE_DAYS:
                continue
            last = u.day
            i = index.get(u.day)
            if i is None or i + UNLOCK_PRIMARY[0] < 0:
                continue
            if g.age[i + UNLOCK_PRIMARY[0], j] < MIN_SPOT_DAYS + 1:
                continue
            events.append(Unlock(coin, u.day, u.tokens, u.insider_tokens))
    return events, every


@dataclass
class UnlockSplits:
    insiders: tuple[int, float]
    """(events, mean primary abnormal return) where insiders got at least half."""
    others: tuple[int, float]
    big_for_volume: tuple[int, float]
    """Unlocks worth more of the coin's daily volume than the median event's."""
    small_for_volume: tuple[int, float]


def unlock_splits(g: Grid, events: list[Unlock]) -> UnlockSplits:
    index = {d: i for i, d in enumerate(g.days)}
    col = {s: j for j, s in enumerate(g.symbols)}
    rows = []
    for u in events:
        i, j = index[u.day], col[u.coin]
        x = abnormal(g, j, i, *UNLOCK_PRIMARY)
        if x is None:
            continue
        price, volume = g.close[i - 1, j], g.volume[i - 1, j]
        ratio = u.tokens * price / volume if np.isfinite(volume) and volume > 0 else np.nan
        rows.append((u.day, x, u.insider_tokens >= 0.5 * u.tokens, ratio))

    def mean(sel: list[tuple[date, float]]) -> tuple[int, float]:
        vals = by_day(sel)
        return len(sel), float(vals.mean()) if len(vals) else float("nan")

    ratios = [r for *_, r in rows if np.isfinite(r)]
    median = float(np.median(ratios)) if ratios else np.nan
    return UnlockSplits(
        insiders=mean([(d, x) for d, x, ins, _ in rows if ins]),
        others=mean([(d, x) for d, x, ins, _ in rows if not ins]),
        big_for_volume=mean([(d, x) for d, x, _, r in rows if np.isfinite(r) and r > median]),
        small_for_volume=mean([(d, x) for d, x, _, r in rows if np.isfinite(r) and r <= median]),
    )


def run_token_unlocks(
    panel: pl.DataFrame,
    schedules: pl.DataFrame,
    mapping: dict[str, str],
    holdout: datetime,
    tiers: Sequence[tuple[float, float]],
) -> tuple[EventResult, UnlockSplits, list[Unlock], list[tuple[int, float]]]:
    g = grid(panel, {}, tiers)
    found, every = unlock_events(g, schedules, mapping, holdout)
    last = (holdout - timedelta(days=1)).date()
    result = study(
        g, [Event(u.coin, u.day) for u in found], UNLOCK_PRIMARY, UNLOCK_OTHERS,
        last_day=last if last in g.days else None, verdict=perp_verdict, avoid=every,
    )  # fmt: skip
    path = event_path(g, [Event(u.coin, u.day) for u in found])
    return result, unlock_splits(g, found), found, path


def format_unlocks(r: EventResult, splits: UnlockSplits, coins: int) -> str:
    title = "Token unlocks (note do-coins-fall-into-large-token-unlocks)"
    text = format_result(title, r, primary="day -30 to 0")
    split = [
        "",
        f"  {coins} coins. Insider unlocks (team, investors): {splits.insiders[0]} at "
        f"{splits.insiders[1]:+.2%}; the rest: {splits.others[0]} at {splits.others[1]:+.2%}",
        f"  Large for the coin's daily volume: {splits.big_for_volume[0]} at "
        f"{splits.big_for_volume[1]:+.2%}; small: {splits.small_for_volume[0]} at "
        f"{splits.small_for_volume[1]:+.2%}",
    ]
    head, _, verdict = text.rpartition("\n\nVerdict:")
    return head + "\n".join(split) + "\n\nVerdict:" + verdict


# ---------- saved for the dashboard ----------

PATH_DAYS = 30


def event_path(g: Grid, events: Sequence[Event], days: int = PATH_DAYS) -> list[tuple[int, float]]:
    """The average path around the events, anchored at day 0's open (0): for each day k
    from -days to +days, the mean (over event days) abnormal return between day k's open
    and day 0's, signed so that a run-up into the event climbs to 0 (k < 0: minus the
    abnormal return from k to 0) and what follows is measured from the event (k > 0: the
    abnormal return from 0 to k). Deciding nothing."""
    index = {d: i for i, d in enumerate(g.days)}
    col = {s: j for j, s in enumerate(g.symbols)}
    out: list[tuple[int, float]] = []
    for k in range(-days, days + 1):
        if k == 0:
            out.append((0, 0.0))
            continue
        rows = []
        for e in events:
            i, j = index[e.day], col[e.coin]
            x = abnormal(g, j, i, k, 0) if k < 0 else abnormal(g, j, i, 0, k)
            if x is not None:
                rows.append((e.day, -x if k < 0 else x))
        vals = by_day(rows)
        out.append((k, float(vals.mean()) if len(vals) else float("nan")))
    return out


def exit_rule_tests(r: EventResult) -> dict[str, bool]:
    """The exit-rule notes' tests, one by one (perp listings, token unlocks)."""
    return {
        "at least 50 event days": r.event_days >= 50,
        "mean at most -3%": r.mean <= -0.03,
        "P(< 0) at least 90%": r.p_below >= 0.90,
        "below at least 90% of the placebo means": r.placebo_share_at_or_below <= 0.10,
    }


def coinbase_tests(r: EventResult) -> dict[str, bool]:
    """The Coinbase-listing note's tests: two-sided, so 95% on whichever side the mean is."""
    up = r.mean >= 0
    return {
        "at least 50 event days": r.event_days >= 50,
        "mean at least +3% or at most -3%": abs(r.mean) >= 0.03,
        "P(same sign) at least 95%": (1 - r.p_below if up else r.p_below) >= 0.95,
        "beyond 95% of the placebo means": (
            1 - r.placebo_share_at_or_below if up else r.placebo_share_at_or_below
        )
        <= 0.05,
    }


def save(
    name: str,
    slug: str,
    result: EventResult,
    report: str,
    path: list[tuple[int, float]],
    data_dir: Path | str,
    commit: str | None,
) -> Path:
    """The study's result for the dashboard (``<data>/events/<name>.json``). A rerun of the
    same frozen note replaces it (the rule is fixed; only the code can change it, and the
    commit is recorded)."""
    doc = {
        "name": name,
        "note": slug,
        "measured_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "verdict": result.verdict,
        "events": result.events,
        "event_days": result.event_days,
        "mean": result.mean,
        "p_below": result.p_below,
        "share_negative": result.share_negative,
        "placebo_median": float(np.median(result.placebo_means)) if result.placebo_means else None,
        "placebo_share_at_or_below": result.placebo_share_at_or_below,
        "tests": coinbase_tests(result) if name == "coinbase-listing" else exit_rule_tests(result),
        "path": [{"day": k, "abnormal": None if not np.isfinite(v) else v} for k, v in path],
        "report": report,
    }
    target = Path(data_dir) / "events" / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(target, lambda tmp: tmp.write_text(json.dumps(doc, indent=2)))
    return target


def load_all(data_dir: Path | str) -> list[dict[str, Any]]:
    """Every saved event study, newest first."""
    found = [json.loads(p.read_text()) for p in (Path(data_dir) / "events").glob("*.json")]
    return sorted(found, key=lambda d: d["measured_at"], reverse=True)
