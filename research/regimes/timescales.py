"""Do faster regimes see volatile spells coming? 1h and 4h HMMs against the daily one.

A description, not a trading result: nothing here trades, so nothing is a trial. Pre-holdout
data only (scored 2021-01 to 2025-10). Fixed before running (2026-11-22):

- One model shape at every timescale: 3 states, inputs each bar's log return and log
  20-bar realised volatility (20 bars: the preset's 20 days, scale-free, so an hourly model
  reacts in hours). Refitted at the start of each month (3 restarts, seed 0) and filtered
  forward with inputs up to each bar only. Training window: the preset's 4 years for daily
  bars, 1 year for 4h and 1h (enough bars, and fits that finish).
- "Volatile" at a bar: the most likely state is the most volatile of the three (states are
  sorted by their volatility input after every fit).
- A daily onset: a day the daily model turns volatile, known at that day's close. It was
  *called early* by a faster model if that model was volatile at some bar in the 7 days
  before the onset was known; the lead is the time from the start of the faster model's
  latest volatile spell before the onset to the daily close that confirmed it.
- A faster model's spell is a *false alarm* if no daily close in the 7 days after it starts
  is volatile.

Run: .venv/bin/python research/regimes/timescales.py   (about 30-60 minutes)
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from kestrel import regimes as rg
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore

HOLDOUT = datetime(2025, 11, 1, tzinfo=UTC)
FIRST = datetime(2021, 1, 1, tzinfo=UTC)
VOL_BARS = 20
STATES = 3
WINDOW = timedelta(days=7)


def bar_inputs(bars: pl.DataFrame) -> tuple[list[datetime], np.ndarray]:
    """Each bar's log return and log 20-bar volatility, known at its close."""
    c = pl.col("close")
    ret = (c / c.shift(1)).log()
    df = bars.select(
        "open_time", ret.alias("r"), ret.rolling_std(VOL_BARS).log().alias("v")
    ).drop_nulls()
    df = df.filter(pl.col("v").is_finite())
    return df["open_time"].to_list(), df.select("r", "v").to_numpy()


def months(first: datetime, last: datetime) -> list[datetime]:
    out, m = [], datetime(first.year, first.month, 1, tzinfo=UTC)
    while m <= last:
        out.append(m)
        m = datetime(m.year + m.month // 12, m.month % 12 + 1, 1, tzinfo=UTC)
    return out


def volatile_at_close(
    bars: pl.DataFrame, step: timedelta, train: timedelta
) -> list[tuple[datetime, bool]]:
    """(close time, volatile?) for every scored bar, filtered as the model saw it then."""
    times, x = bar_inputs(bars)
    stamp = np.array([t.timestamp() for t in times])
    out: list[tuple[datetime, bool]] = []
    for m in months(FIRST, HOLDOUT - timedelta(days=1)):
        after = datetime(m.year + m.month // 12, m.month % 12 + 1, 1, tzinfo=UTC)
        tr = np.where((stamp >= (m - train).timestamp()) & (stamp < m.timestamp()))[0]
        te = np.where((stamp >= m.timestamp()) & (stamp < after.timestamp()))[0]
        if len(te) == 0 or len(tr) < 0.9 * (train / step):
            continue
        model = rg.fit(x[tr], STATES, restarts=3, seed=0)
        alpha, _ = rg.filtered(model, x[tr[0] : te[-1] + 1])
        for i, p in zip(te, alpha[len(tr) :], strict=True):
            out.append((times[i] + step, int(np.argmax(p)) == STATES - 1))
        print(f"    {m:%Y-%m}", end="\r", flush=True)
    return out


def spells(flags: list[tuple[datetime, bool]]) -> list[tuple[datetime, datetime]]:
    """(start, end) of each unbroken run of volatile bars, by close time."""
    out: list[tuple[datetime, datetime]] = []
    start: datetime | None = None
    prev: datetime | None = None
    for t, v in flags:
        if v and start is None:
            start = t
        if not v and start is not None and prev is not None:
            out.append((start, prev))
            start = None
        prev = t
    if start is not None and prev is not None:
        out.append((start, prev))
    return out


@dataclass(frozen=True)
class Comparison:
    timescale: str
    share_volatile: float
    spells: int
    median_spell_hours: float
    onsets_called_early: float
    """Share of daily onsets this model was volatile in the 7 days before."""
    median_lead_hours: float | None
    false_alarms: float
    """Share of this model's spells with no volatile daily close in the 7 days after."""


def compare(
    name: str, fast: list[tuple[datetime, bool]], daily: list[tuple[datetime, bool]]
) -> Comparison:
    fast_spells = spells(fast)
    daily_onsets = [s for s, _ in spells(daily)]
    volatile_days = [t for t, v in daily if v]
    leads: list[float] = []
    for onset in daily_onsets:
        before = [s for s, e in fast_spells if s < onset and e >= onset - WINDOW]
        if before:
            leads.append((onset - max(before)).total_seconds() / 3600)
    false = 0
    for s, _ in fast_spells:
        if not any(s <= d <= s + WINDOW for d in volatile_days):
            false += 1
    bar = {"1h": 1, "4h": 4}.get(name, 24)
    hours = [(e - s).total_seconds() / 3600 + bar for s, e in fast_spells]
    return Comparison(
        name,
        sum(v for _, v in fast) / len(fast),
        len(fast_spells),
        statistics.median(hours) if hours else math.nan,
        len(leads) / len(daily_onsets) if daily_onsets else math.nan,
        statistics.median(leads) if leads else None,
        false / len(fast_spells) if fast_spells else math.nan,
    )


def main() -> None:
    store = ParquetStore("data")
    for symbol in ("BTCUSDT", "ETHUSDT"):
        print(f"\n{symbol}: scored {FIRST:%Y-%m} to 2025-10", flush=True)
        found = {}
        for name, iv, step, train in (
            ("1d", Interval.D1, timedelta(days=1), timedelta(days=4 * 365)),
            ("4h", Interval.H4, timedelta(hours=4), timedelta(days=365)),
            ("1h", Interval.H1, timedelta(hours=1), timedelta(days=365)),
        ):
            bars = store.read(symbol, iv, None, HOLDOUT)
            print(f"  fitting {name}...", flush=True)
            found[name] = volatile_at_close(bars, step, train)
        daily = found["1d"]
        print(f"  daily onsets: {len(spells(daily))}")
        print(
            "  timescale  volatile  spells  median spell  daily onsets called early"
            "  median lead  false alarms"
        )
        for name in ("1d", "4h", "1h"):
            c = compare(name, found[name], daily)
            lead = "n/a" if c.median_lead_hours is None else f"{c.median_lead_hours:.0f} h"
            print(
                f"  {name:9s} {c.share_volatile:8.0%} {c.spells:7d} {c.median_spell_hours:10.0f} h"
                f" {c.onsets_called_early:26.0%} {lead:>12s} {c.false_alarms:13.0%}",
                flush=True,
            )


if __name__ == "__main__":
    main()
