"""Cross-sectional raw-effect studies on Sunday closes (2026-11-16, after an outside review
suggested market-structure signals): does a coin's funding or its taker flow predict how
it does next week against the other coins, beyond its own past week and its size?

Each Sunday close (the daily bar opening Sunday 00:00 UTC, closing Monday 00:00), for each
coin with at least ``MIN_DAYS`` days of history and a 30-day median daily quote volume of
at least ``MIN_VOLUME`` (the basket rule, so the coins are tradable):

- ``ret_next``: log return from this close to the close 7 days later (a missing bar on
  either day leaves the week out for that coin);
- ``ret_past``: log return over the 7 days to this close;
- ``size``: log of the 30-day median daily quote volume;
- ``taker``: the share of the last 7 days' quote volume bought by takers (Binance's own
  split of each bar's volume);
- ``funding``: the mean funding rate of the coin's USDT-M perpetual over settlements in the
  7 days before the close (strictly before it), or null with no perp or no settlements.

Two tests, both on a signal oriented so that higher is better (funding enters as minus
funding: crowded longs pay to hold, so low funding is the uncrowded side):

1. Each week, next-week returns are regressed across coins on the z-scored signal, past
   week and size; the signal's coefficient is averaged over weeks (Fama and MacBeth 1973),
   with a bootstrap over weeks for P(mean > 0).
2. Long-only: each week the top fifth of coins by signal against the average eligible
   coin; the mean weekly excess must clear one round trip of costs (the hurdle).

Weeks with fewer than ``MIN_COINS`` coins carrying the signal are left out. Never the
holdout.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

import numpy as np
import polars as pl

MIN_DAYS = 180
MIN_VOLUME = 20_000_000.0
MIN_COINS = 10
RESAMPLES = 5000
CONFIDENCE = 0.90
Signal = Literal["taker-flow", "funding"]


def coin_weeks(
    symbol: str, bars: pl.DataFrame, funding: pl.DataFrame | None = None
) -> pl.DataFrame:
    """One row per Sunday close for one coin, with every column the studies use."""
    d = (
        bars.sort("open_time")
        .with_columns(day=pl.col("open_time").dt.date())
        .with_columns(
            age=pl.int_range(1, pl.len() + 1),
            median_volume=pl.col("quote_volume").rolling_median(30, min_samples=30),
            buy7=pl.col("taker_buy_quote_volume").rolling_sum(7, min_samples=7),
            vol7=pl.col("quote_volume").rolling_sum(7, min_samples=7),
        )
    )
    closes = dict(zip(d["day"].to_list(), d["close"].to_list(), strict=True))
    sundays = d.filter(pl.col("open_time").dt.weekday() == 7)
    rows = []
    for r in sundays.iter_rows(named=True):
        day: date = r["day"]
        now, before, after = (
            r["close"],
            closes.get(day - timedelta(days=7)),
            closes.get(day + timedelta(days=7)),
        )
        close_at = datetime.combine(
            day + timedelta(days=1), datetime.min.time(), tzinfo=r["open_time"].tzinfo
        )
        rate = None
        if funding is not None and not funding.is_empty():
            window = funding.filter(
                (pl.col("time") > close_at - timedelta(days=7)) & (pl.col("time") < close_at)
            )
            rate = float(window["rate"].mean()) if window.height else None  # type: ignore[arg-type]
        rows.append(
            {
                "week": day,
                "symbol": symbol,
                "eligible": bool(
                    r["age"] >= MIN_DAYS
                    and r["median_volume"] is not None
                    and r["median_volume"] >= MIN_VOLUME
                ),
                "ret_next": math.log(after / now) if after and now else None,
                "ret_past": math.log(now / before) if before and now else None,
                "size": math.log(r["median_volume"]) if r["median_volume"] else None,
                "taker": r["buy7"] / r["vol7"] if r["vol7"] else None,
                "funding": rate,
            }
        )
    schema = {
        "week": pl.Date,
        "symbol": pl.String,
        "eligible": pl.Boolean,
        "ret_next": pl.Float64,
        "ret_past": pl.Float64,
        "size": pl.Float64,
        "taker": pl.Float64,
        "funding": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema, orient="row")


def signal_column(signal: Signal) -> pl.Expr:
    """The signal, oriented so that higher is expected to be better."""
    return pl.col("taker") if signal == "taker-flow" else -pl.col("funding")


def _z(x: np.ndarray) -> np.ndarray:
    sd = x.std()
    return (x - x.mean()) / sd if sd > 0 else np.zeros_like(x)


@dataclass(frozen=True)
class Week:
    week: date
    coins: int
    coefficient: float
    """The signal's slope on next-week log return, per standard deviation of the signal."""
    excess: float
    """The top fifth's mean next-week log return less the average eligible coin's."""
    momentum_overlap: float
    """Correlation between the signal and the past week's return across coins."""


def weeks(panel: pl.DataFrame, signal: Signal) -> list[Week]:
    usable = (
        panel.filter(pl.col("eligible"))
        .with_columns(signal=signal_column(signal))
        .drop_nulls(["signal", "ret_next", "ret_past", "size"])
    )
    out: list[Week] = []
    for (week,), g in usable.sort("week").group_by("week", maintain_order=True):
        if g.height < MIN_COINS:
            continue
        s, past, size = g["signal"].to_numpy(), g["ret_past"].to_numpy(), g["size"].to_numpy()
        y = g["ret_next"].to_numpy()
        x = np.column_stack([np.ones(len(y)), _z(s), _z(past), _z(size)])
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        top = y[s >= np.quantile(s, 0.8)]
        overlap = float(np.corrcoef(s, past)[0, 1]) if s.std() > 0 and past.std() > 0 else 0.0
        excess = float(top.mean() - y.mean())
        out.append(Week(week, len(y), float(beta[1]), excess, overlap))
    return out


def share_above_zero(values: list[float], seed: int = 0) -> float:
    rng = random.Random(seed)
    n = len(values)
    if n == 0:
        return 0.0
    above = sum(sum(rng.choice(values) for _ in range(n)) / n > 0 for _ in range(RESAMPLES))
    return above / RESAMPLES


@dataclass(frozen=True)
class CrossResult:
    signal: Signal
    coins: int
    weeks: list[Week]
    hurdle: float

    @property
    def mean_coefficient(self) -> float:
        return float(np.mean([w.coefficient for w in self.weeks])) if self.weeks else 0.0

    @property
    def p_coefficient(self) -> float:
        return share_above_zero([w.coefficient for w in self.weeks])

    @property
    def mean_excess(self) -> float:
        return float(np.mean([w.excess for w in self.weeks])) if self.weeks else 0.0

    @property
    def p_excess(self) -> float:
        return share_above_zero([w.excess for w in self.weeks], seed=1)

    @property
    def verdict(self) -> str:
        ok = (
            self.mean_coefficient > 0
            and self.p_coefficient >= CONFIDENCE
            and self.mean_excess > self.hurdle
        )
        return "PROCEED" if ok else "KILL"


def run_cross_study(panel: pl.DataFrame, signal: Signal, hurdle: float) -> CrossResult:
    found = weeks(panel, signal)
    if not found:
        raise ValueError(f"no week had {MIN_COINS}+ eligible coins with the {signal} signal")
    return CrossResult(signal, panel["symbol"].n_unique(), found, hurdle)


def format_cross_study(r: CrossResult) -> str:
    def pct(x: float) -> str:
        return f"{math.expm1(x):+.2%}"

    coins = [w.coins for w in r.weeks]
    taker = r.signal == "taker-flow"
    what = "taker-buy share of the last 7 days" if taker else "minus 7-day funding"
    overlap = float(np.mean([w.momentum_overlap for w in r.weeks]))
    return "\n".join(
        [
            f"Cross-sectional study  ·  {r.signal} ({what})  ·  Sunday closes, next week",
            f"{len(r.weeks)} weeks, {r.weeks[0].week} to {r.weeks[-1].week}; "
            f"{min(coins)}-{max(coins)} eligible coins a week (median {int(np.median(coins))})",
            "",
            "  Signal's weekly slope (controls: past week, size): "
            f"{r.mean_coefficient:+.4%} a week per SD, P(> 0) {r.p_coefficient:.0%} (90% needed)",
            f"  Top fifth vs the average coin: {pct(r.mean_excess)} a week, "
            f"P(> 0) {r.p_excess:.0%}; hurdle {pct(r.hurdle)} (one round trip)",
            f"  Overlap with the past week's return (correlation): {overlap:+.2f} on average",
            "",
            f"Verdict: {r.verdict}",
        ]
    )


def perp_funding(data_dir: str, coin: str) -> pl.DataFrame | None:
    """A coin's stored perp funding (XUSDT, or 1000XUSDT), or None if none is stored."""
    from kestrel.data import funding

    for name in (coin, f"1000{coin}"):
        try:
            return funding.load(data_dir, name)
        except ValueError:
            continue
    return None


def build_panel(data_dir: str, coins: list[str], holdout: datetime) -> pl.DataFrame:
    """Every coin's Sunday rows, before the holdout."""
    from kestrel.data.schema import Interval
    from kestrel.data.store import ParquetStore

    store = ParquetStore(data_dir)
    frames = []
    for coin in sorted(coins):
        bars = store.read(coin, Interval.D1, None, holdout)
        if bars.height < 2:
            continue
        frames.append(coin_weeks(coin, bars, perp_funding(data_dir, coin)))
    if not frames:
        raise ValueError("no stored daily bars for the study's coins")
    return pl.concat(frames)
