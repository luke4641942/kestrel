"""Which HMM describes BTC and ETH best? Variants compared by out-of-sample likelihood only.

docs/rules.md: other regime inputs are compared by out-of-sample log-likelihood, never by
trading results. Nothing here trades, so nothing here is a trial; it chooses a better
*description*, not an edge. Pre-holdout data only (to 2025-10).

Every variant walks forward like ``kestrel.regimes.walk_forward``: refit at the start of
each month on the training window before it (10 restarts, seed 0), then predict each day
of the month one step ahead. All variants are scored on the same months, the latest first
month any of them can reach, and on the same two shared inputs (the day's log return and
log 20-day volatility): extra inputs only count through what they teach the filter
(``predictive_log_likelihood``). A difference of a few nats over ~1,200 days is noise; the
per-day gain is printed so it reads as a size.

Run: .venv/bin/python research/regimes/compare.py   (about an hour)
     .venv/bin/python research/regimes/compare.py --returns-only   (returns scored alone)
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from kestrel import regimes as rg
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore

HOLDOUT = datetime(2025, 11, 1, tzinfo=UTC)
BASE = [0, 1]  # return, log_volatility: the columns every variant is scored on
RETURNS_ONLY = "--returns-only" in sys.argv
"""Score only the return column: does a variant predict returns' spread better, not just
tomorrow's (very persistent) 20-day volatility? The extra-input variants are left out."""


def extras_volume(bars: pl.DataFrame) -> pl.Series:
    """Log of the day's quote volume against its 30-day average (known at the close)."""
    qv = pl.col("quote_volume")
    return bars.select((qv / qv.rolling_mean(30)).log().alias("volume"))["volume"]


def extras_drawdown(bars: pl.DataFrame) -> pl.Series:
    """Log distance below the highest close of the last 365 days."""
    c = pl.col("close")
    return bars.select((c / c.rolling_max(365)).log().alias("drawdown"))["drawdown"]


def extras_btc(btc: pl.DataFrame) -> Callable[[pl.DataFrame], list[pl.Series]]:
    """BTC's own return and log volatility, joined by day (for modelling ETH with BTC)."""

    def make(bars: pl.DataFrame) -> list[pl.Series]:
        b = rg.inputs(btc).rename({"return": "btc_return", "log_volatility": "btc_vol"})
        joined = bars.select("open_time").join(b, on="open_time", how="left")
        return [joined["btc_return"], joined["btc_vol"]]

    return make


@dataclass(frozen=True)
class Variant:
    name: str
    states: int = 3
    train_days: int = 4 * 365
    extras: Callable[[pl.DataFrame], list[pl.Series]] | None = None


def frame(bars: pl.DataFrame, v: Variant) -> tuple[list[datetime], np.ndarray]:
    base = rg.inputs(bars)
    cols = [base["return"], base["log_volatility"]]
    if v.extras is not None:
        cols += v.extras(bars)
    df = pl.DataFrame({"open_time": base["open_time"], **{f"c{i}": c for i, c in enumerate(cols)}})
    df = df.drop_nulls().filter(pl.all_horizontal(pl.exclude("open_time").is_finite()))
    return df["open_time"].to_list(), df.drop("open_time").to_numpy()


def months(first: datetime, last: datetime) -> list[datetime]:
    out, m = [], datetime(first.year, first.month, 1, tzinfo=UTC)
    while m <= last:
        out.append(m)
        m = datetime(m.year + m.month // 12, m.month % 12 + 1, 1, tzinfo=UTC)
    return out


def first_month(times: list[datetime], train_days: int) -> datetime:
    """The first month with 90% of a training window behind it."""
    for m in months(times[0], times[-1]):
        if sum(1 for t in times if m - timedelta(days=train_days) <= t < m) >= 0.9 * train_days:
            return m
    raise ValueError("not enough history")


def score(
    times: list[datetime], x: np.ndarray, v: Variant, scored: list[datetime]
) -> tuple[float, int]:
    total, days = 0.0, 0
    for m in scored:
        after = datetime(m.year + m.month // 12, m.month % 12 + 1, 1, tzinfo=UTC)
        train = [i for i, t in enumerate(times) if m - timedelta(days=v.train_days) <= t < m]
        test = [i for i, t in enumerate(times) if m <= t < after]
        if not test:
            continue
        model = rg.fit(x[train], v.states, restarts=10, seed=0)
        span = x[train[0] : test[-1] + 1]
        total += rg.predictive_log_likelihood(
            model, span, len(train), [0] if RETURNS_ONLY else BASE
        )
        days += len(test)
    return total, days


def main() -> None:
    store = ParquetStore("data")
    btc = store.read("BTCUSDT", Interval.D1, None, HOLDOUT)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        bars = store.read(symbol, Interval.D1, None, HOLDOUT)
        variants = [
            Variant("2 states", states=2),
            Variant("3 states (the preset)"),
            Variant("4 states", states=4),
            Variant("5 states", states=5),
            Variant("3 states, 2-year window", train_days=2 * 365),
            Variant("3 states, 3-year window", train_days=3 * 365),
            Variant("3 states + volume", extras=lambda b: [extras_volume(b)]),
            Variant("3 states + drawdown from 1y high", extras=lambda b: [extras_drawdown(b)]),
        ]
        if symbol != "BTCUSDT":
            variants.append(Variant("3 states + BTC's inputs", extras=extras_btc(btc)))
        if RETURNS_ONLY:
            variants = [v for v in variants if v.extras is None]
        frames = {v.name: frame(bars, v) for v in variants}
        start = max(first_month(frames[v.name][0], v.train_days) for v in variants)
        scored = months(start, HOLDOUT - timedelta(days=1))
        print(f"\n{symbol}: scored {scored[0]:%Y-%m} to {scored[-1]:%Y-%m}", flush=True)
        results = []
        for v in variants:
            times, x = frames[v.name]
            total, days = score(times, x, v, scored)
            results.append((v.name, total, days))
            print(f"  {v.name:36s} {total:10.1f} nats over {days} days", flush=True)
        preset = next(r for r in results if r[0].startswith("3 states (the"))
        print(
            "  Against the preset, per day "
            f"(nats; +0.01 a day is ~{math.exp(0.01) - 1:.0%} likelier):"
        )
        for name, total, days in results:
            print(f"    {name:36s} {(total - preset[1]) / days:+.4f}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
