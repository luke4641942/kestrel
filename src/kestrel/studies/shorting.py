"""Hypothetical shorting studies (TODO.md, "Shorting research"; Luke, 2026-11-16).

Kestrel can't short: the engine's weights are in [0, 1]. These studies measure, outside the
engine, what a short side would have added, each pre-registered in a frozen note and run
once. A pass is recorded as needs-shorting, never traded.

One daily simulator serves all five (``simulate``): a weight decided at day t's close is
held from day t+1's open to day t+2's open (the engine's convention), rebalanced daily to
its target, so each day's return is ``weight x (next open / open - 1)``. Costs, on every
change in weight, at the band known at the previous close:

- long side (spot): the taker fee plus the coin's half-spread band (kestrel.toml);
- short side: as a CFD (2x the band, plus financing on short notional each day) or as a
  Binance USDT-M perp (a 5 bps taker fee plus the band, plus the coin's real funding, which
  pays a short when positive).

A short is closed out if a day's high reaches 1.5x that day's open (a conservative margin
call at 2:1): the day loses half its notional. A coin with no next open (delisted) is
closed at its last close and held no longer. The long-only rule each short side is
compared with runs through the same code, so the comparison is like for like (the engine
itself holds quantities and has a no-trade band; neither is modelled here, for either).
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl

from kestrel.backtest.spread import with_half_spread

DAYS_PER_YEAR = 365
START = datetime(2020, 1, 1, tzinfo=UTC)
"""First day scored: Binance's funding history starts here."""
PERP_FEE = 5e-4
"""Binance USDT-M taker fee at the base tier (an assumption the notes record)."""
CFD_FINANCING = 0.05
CFD_STRESS = 0.15
CLOSE_OUT = 1.5
BLOCK = 20
RESAMPLES = 5000
YEARS = (2020, 2021, 2022, 2023, 2024, 2025)
ShortVenue = Literal["cfd", "cfd-stress", "perp"]


# ---------- data ----------


@dataclass(frozen=True)
class Grid:
    """Daily data for a set of coins, one row per day, one column per coin."""

    days: list[date]
    symbols: list[str]
    open: np.ndarray
    high: np.ndarray
    close: np.ndarray
    band: np.ndarray
    """Half-spread known at each day's close (a fraction)."""
    funding: np.ndarray
    """Sum of perp funding settlements during each day (open to next open); 0 if none."""
    volume: np.ndarray
    """Median daily quote volume over the last 30 days, known at each day's close."""
    age: np.ndarray
    """Days of unbroken history up to each day (0 where there is no bar)."""
    perp: np.ndarray
    """True where the coin's perp settled funding that day (it was shortable)."""


def grid(
    panel: pl.DataFrame,
    funding: dict[str, pl.DataFrame | None],
    tiers: Sequence[tuple[float, float]],
    window: int = 30,
) -> Grid:
    """``panel``: daily bars of several coins (symbol, history, open_time, open, high,
    close, quote_volume), as kestrel.data.panel.load_panel returns. ``funding``: each
    coin's perp funding (time, rate), or None."""
    priced = with_half_spread(
        panel.sort("symbol", "history", "open_time"), window, tiers, ["symbol", "history"]
    ).with_columns(
        median_volume=pl.col("quote_volume")
        .rolling_median(window, min_samples=window)
        .over(["symbol", "history"]),
        age=pl.int_range(1, pl.len() + 1).over(["symbol", "history"]),
        day=pl.col("open_time").dt.date(),
    )
    days = sorted(set(priced["day"].to_list()))
    symbols = sorted(set(priced["symbol"].to_list()))
    index = {d: i for i, d in enumerate(days)}
    col = {s: j for j, s in enumerate(symbols)}
    shape = (len(days), len(symbols))
    out = {k: np.full(shape, np.nan) for k in ("open", "high", "close", "band", "volume")}
    age = np.zeros(shape)
    for row in priced.select(
        "day", "symbol", "open", "high", "close", "half_spread", "median_volume", "age"
    ).iter_rows():
        i, j = index[row[0]], col[row[1]]
        for k, v in zip(("open", "high", "close", "band", "volume"), row[2:7], strict=True):
            out[k][i, j] = np.nan if v is None else v
        age[i, j] = row[7]
    fund = np.zeros(shape)
    perp = np.zeros(shape, dtype=bool)
    for s, f in funding.items():
        if f is None or s not in col:
            continue
        daily = f.group_by(pl.col("time").dt.date().alias("day")).agg(pl.col("rate").sum())
        for d, rate in daily.iter_rows():
            if d in index:
                fund[index[d], col[s]] = rate
                perp[index[d], col[s]] = True
    return Grid(
        days, symbols, out["open"], out["high"], out["close"], out["band"], fund,
        out["volume"], age, perp,
    )  # fmt: skip


# ---------- the simulator ----------


@dataclass(frozen=True)
class Sim:
    days: list[date]
    total: np.ndarray
    """Daily return of the whole account, after every cost."""
    long_leg: np.ndarray
    short_leg: np.ndarray
    """Each leg's daily contribution, after its own costs."""
    short_gross: np.ndarray
    short_costs: np.ndarray
    """The short leg before costs, and its costs (fees, spread, financing; funding paid
    counts as a cost, funding received as gross)."""
    long_gross: np.ndarray
    long_costs: np.ndarray
    short_trades: int
    long_trades: int
    close_outs: int


def simulate(
    g: Grid,
    weights: np.ndarray,
    fee: float,
    short_venue: ShortVenue = "cfd",
    start: datetime = START,
) -> Sim:
    """Daily returns of ``weights`` (decided at each day's close, rows and columns as
    ``g``), scored from ``start``. The last day (no next open) is dropped."""
    w = np.nan_to_num(weights.astype(float))
    pos = np.zeros_like(w)
    pos[1:] = w[:-1]
    has_open = np.isfinite(g.open)
    pos[~has_open] = 0.0
    nxt = np.full_like(g.open, np.nan)
    nxt[:-1] = g.open[1:]
    ret = np.where(np.isfinite(nxt), nxt / g.open - 1, g.close / g.open - 1)
    ret = np.nan_to_num(ret)
    # A coin that has gone (no next open) is closed at its last close: hold nothing after.
    prev = np.zeros_like(pos)
    prev[1:] = pos[:-1]
    band = np.zeros_like(pos)
    band[1:] = np.nan_to_num(g.band[:-1], nan=float(np.nanmax(g.band)))
    longs, shorts = np.maximum(pos, 0), np.minimum(pos, 0)
    prev_l, prev_s = np.maximum(prev, 0), np.minimum(prev, 0)
    long_cost = np.abs(longs - prev_l) * (fee + band)
    if short_venue == "perp":
        trade = np.abs(shorts - prev_s) * (PERP_FEE + band)
        carry = -shorts * g.funding  # a positive rate pays the short
    else:
        rate = CFD_FINANCING if short_venue == "cfd" else CFD_STRESS
        trade = np.abs(shorts - prev_s) * 2 * band
        carry = shorts * rate / DAYS_PER_YEAR  # financing charged on short notional
    spike = (shorts < 0) & (np.nan_to_num(g.high / g.open) >= CLOSE_OUT)
    short_move = np.where(spike, shorts * (CLOSE_OUT - 1), shorts * ret)
    short_gross = short_move + np.maximum(carry, 0)
    short_costs = trade + np.maximum(-carry, 0)
    long_gross = longs * ret
    keep = np.array([d >= start.date() for d in g.days])
    keep[-1] = False
    entered_s = (shorts < 0) & (prev_s == 0)
    entered_l = (longs > 0) & (prev_l == 0)
    ls, ll = short_gross - short_costs, long_gross - long_cost
    return Sim(
        days=[d for d, k in zip(g.days, keep, strict=True) if k],
        total=(ls + ll).sum(axis=1)[keep],
        long_leg=ll.sum(axis=1)[keep],
        short_leg=ls.sum(axis=1)[keep],
        short_gross=short_gross.sum(axis=1)[keep],
        short_costs=short_costs.sum(axis=1)[keep],
        long_gross=long_gross.sum(axis=1)[keep],
        long_costs=long_cost.sum(axis=1)[keep],
        short_trades=int(entered_s[keep].sum()),
        long_trades=int(entered_l[keep].sum()),
        close_outs=int(spike[keep].sum()),
    )


# ---------- measures ----------


def sharpe(r: np.ndarray) -> float:
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    return float(np.mean(r)) / sd * math.sqrt(DAYS_PER_YEAR) if sd > 0 else 0.0


def max_drawdown(r: np.ndarray) -> float:
    if not len(r):
        return 0.0
    eq = np.cumprod(1 + r)
    return float(np.min(eq / np.maximum.accumulate(eq) - 1))


def growth(r: np.ndarray) -> float:
    return float(np.prod(1 + r) - 1)


def p_positive(r: np.ndarray, block: int = BLOCK, seed: int = 0) -> float:
    """Block bootstrap of the mean (circular blocks of ``block`` observations): the share
    of resamples whose mean is above zero."""
    n = len(r)
    if n < 2:
        return 0.0
    rng = np.random.default_rng(seed)
    blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=(RESAMPLES, blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(RESAMPLES, -1)[:, :n]
    means = r[idx % n].mean(axis=1)
    return float((means > 0).mean())


def by_year(days: list[date], r: np.ndarray) -> dict[int, float]:
    years = np.array([d.year for d in days])
    return {y: sharpe(r[years == y]) for y in YEARS if (years == y).sum() > 30}


def weekly(days: list[date], r: np.ndarray) -> np.ndarray:
    """Daily returns compounded into Monday-to-Sunday weeks."""
    frame = pl.DataFrame({"day": days, "r": r}).with_columns(week=pl.col("day").dt.truncate("1w"))
    return (
        frame.group_by("week")
        .agg(((pl.col("r") + 1).product() - 1).alias("r"))
        .sort("week")["r"]
        .to_numpy()
    )


def beta(r: np.ndarray, market: np.ndarray) -> float:
    v = float(np.var(market, ddof=1))
    return float(np.cov(r, market, ddof=1)[0, 1]) / v if v > 0 else 0.0


# ---------- short legs on an existing long rule (notes 1-3) ----------


@dataclass
class LegResult:
    market: str
    days: int
    first: date
    last: date
    sharpe: float
    long_only_sharpe: float
    ret: float
    long_only_ret: float
    max_drawdown: float
    short_leg_return: float
    short_leg_p: float
    years: dict[int, tuple[float, float]]
    """Year -> (long/short Sharpe, long-only Sharpe)."""
    short_break_even: float | None
    """Cost multiple at which the short leg breaks even (None: its gross is negative)."""
    short_trades: int
    close_outs: int
    beta: float
    correlation: float
    perp_sharpe: float
    perp_short_leg_return: float
    stress_sharpe: float
    skew: float
    kurtosis: float
    deflated: float | None = None
    tests: dict[str, bool] = field(default_factory=dict)

    @property
    def years_beaten(self) -> int:
        return sum(ls > lo for ls, lo in self.years.values())

    @property
    def passes(self) -> bool:
        return all(self.tests.values())


def leg_result(
    market: str,
    g: Grid,
    long_short: np.ndarray,
    long_only: np.ndarray,
    fee: float,
    btc_returns: np.ndarray,
) -> LegResult:
    ls = simulate(g, long_short, fee, "cfd")
    lo = simulate(g, long_only, fee, "cfd")
    perp = simulate(g, long_short, fee, "perp")
    stress = simulate(g, long_short, fee, "cfd-stress")
    gross, costs = float(ls.short_gross.sum()), float(ls.short_costs.sum())
    a, b = by_year(ls.days, ls.total), by_year(lo.days, lo.total)
    r = ls.total
    centred = r - r.mean()
    sd = r.std()
    result = LegResult(
        market=market,
        days=len(r),
        first=ls.days[0],
        last=ls.days[-1],
        sharpe=sharpe(r),
        long_only_sharpe=sharpe(lo.total),
        ret=growth(r),
        long_only_ret=growth(lo.total),
        max_drawdown=max_drawdown(r),
        short_leg_return=growth(ls.short_leg),
        short_leg_p=p_positive(ls.short_leg),
        years={y: (a[y], b[y]) for y in a if y in b},
        short_break_even=gross / costs if costs > 0 and gross > 0 else None,
        short_trades=ls.short_trades,
        close_outs=ls.close_outs,
        beta=beta(r, btc_returns),
        correlation=float(np.corrcoef(r, lo.total)[0, 1]),
        perp_sharpe=sharpe(perp.total),
        perp_short_leg_return=growth(perp.short_leg),
        stress_sharpe=sharpe(stress.total),
        skew=float((centred**3).mean() / sd**3) if sd > 0 else 0.0,
        kurtosis=float((centred**4).mean() / sd**4) if sd > 0 else 3.0,
    )
    result.tests = {
        "beats long-only on Sharpe": result.sharpe > result.long_only_sharpe,
        "short leg positive (P >= 90%)": result.short_leg_return > 0 and result.short_leg_p >= 0.9,
        "beats long-only in 4 of 6 years": result.years_beaten >= 4,
        "drawdown within 50%": result.max_drawdown >= -0.5,
    }
    return result


def size(close: np.ndarray, vol_days: int = 30, target: float = 0.5) -> np.ndarray:
    """min(1, target / annualised volatility of the last ``vol_days`` daily log returns),
    per column; 0 where volatility isn't known yet (as the pursued strategies do)."""
    logr = np.full_like(close, np.nan)
    logr[1:] = np.log(close[1:] / close[:-1])
    vol = pl.DataFrame(logr).select(
        pl.all().rolling_std(vol_days, min_samples=vol_days)
    ).to_numpy() * math.sqrt(DAYS_PER_YEAR)
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.minimum(1.0, target / vol)
    return np.where(np.isfinite(s) & (vol > 0), s, 0.0)


def shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(a, np.nan)
    out[k:] = a[:-k]
    return out


def regime_trend_weights(
    close: np.ndarray, btc: np.ndarray, btc_days: int = 50, coin_days: int = 30
) -> tuple[np.ndarray, np.ndarray]:
    """btc-regime-trend's long weight (unchanged) and the note's long/short weight (its
    mirror added), for one coin column. ``btc``: BTC's close on the same days."""
    ma = pl.Series(btc).rolling_mean(btc_days, min_samples=btc_days).to_numpy()
    past = shift(close, coin_days)
    s = size(close[:, None])[:, 0]
    with np.errstate(invalid="ignore"):
        up = (btc > ma) & (close > past)
        down = (btc < ma) & (close < past)
    long_only = np.where(up, s, 0.0)
    return long_only, long_only - np.where(down, s, 0.0)


def trend_votes_weights(
    close: np.ndarray, lookbacks: tuple[int, int, int] = (20, 60, 120)
) -> tuple[np.ndarray, np.ndarray]:
    """trend-vol-target's weight (up votes / 3 x size) and the symmetric one
    ((up - down) / 3 x size), for one coin column; cash until every lookback has filled."""
    s = size(close[:, None])[:, 0]
    up = np.zeros_like(close)
    down = np.zeros_like(close)
    ready = np.ones_like(close, dtype=bool)
    for k in lookbacks:
        past = shift(close, k)
        ready &= np.isfinite(past)
        with np.errstate(invalid="ignore"):
            up += close > past
            down += close < past
    long_only = np.where(ready, up / 3 * s, 0.0)
    return long_only, np.where(ready, (up - down) / 3 * s, 0.0)


# ---------- baskets: G1's short side and long/short momentum (notes 2 and 4) ----------


def sunday_weights(
    g: Grid,
    choose: Callable[[int], dict[str, float]],
) -> np.ndarray:
    """Weights decided at each Sunday close by ``choose(day index)`` and held through the
    week (until the next Sunday's decision)."""
    w = np.zeros_like(g.open)
    col = {s: j for j, s in enumerate(g.symbols)}
    current = np.zeros(len(g.symbols))
    for i, d in enumerate(g.days):
        if d.isoweekday() == 7:
            current = np.zeros(len(g.symbols))
            for s, x in choose(i).items():
                current[col[s]] = x
        w[i] = current
    return w


def momentum(g: Grid, i: int, lookback: int) -> np.ndarray:
    """Each coin's ``lookback``-day return at day i's close (nan if unknown)."""
    if i < lookback:
        return np.full(len(g.symbols), np.nan)
    out: np.ndarray = g.close[i] / g.close[i - lookback] - 1
    return out


def rotation_weights(
    g: Grid,
    btc_index: int,
    top_n: int = 3,
    lookback: int = 90,
    btc_days: int = 50,
    margin: float = 0.03,
    short_side: bool = True,
) -> np.ndarray:
    """G1: long the top ``top_n`` while BTC is ``margin`` above its ``btc_days`` average;
    with ``short_side``, short the bottom ``top_n`` while BTC is ``margin`` below it. Each
    slot 1/top_n x min(1, 50% / volatility); a coin needs a year of unbroken history."""
    btc = g.close[:, btc_index]
    ma = pl.Series(btc).rolling_mean(btc_days, min_samples=btc_days).to_numpy()
    sizes = size(g.close)

    def choose(i: int) -> dict[str, float]:
        if not np.isfinite(ma[i]):
            return {}
        on = btc[i] > ma[i] * (1 + margin)
        off = btc[i] < ma[i] * (1 - margin)
        if not (on or (off and short_side)):
            return {}
        r = momentum(g, i, lookback)
        ok = np.isfinite(r) & (g.age[i] >= 365) & (sizes[i] > 0)
        eligible = [j for j in range(len(g.symbols)) if ok[j]]
        # Ties: G1 sorts by return then symbol; the shorts take the weakest.
        ranked = sorted(eligible, key=lambda j: (-r[j], g.symbols[j]))
        picks = ranked[:top_n] if on else ranked[::-1][:top_n]
        sign = 1.0 if on else -1.0
        return {g.symbols[j]: sign * sizes[i, j] / top_n for j in picks}

    return sunday_weights(g, choose)


def long_short_momentum_weights(
    g: Grid,
    top: int = 3,
    lookback: int = 90,
    min_volume: float = 20_000_000.0,
) -> np.ndarray:
    """Note 4: each Sunday, among coins with 20M+ median volume, a year of history and a
    perp that settled funding that week, long the top 3 and short the bottom 3 by 90-day
    return, each 1/6 x min(1, 50% / volatility). Fewer than 6 eligible: cash."""
    sizes = size(g.close)

    def choose(i: int) -> dict[str, float]:
        r = momentum(g, i, lookback)
        week = g.perp[max(0, i - 6) : i + 1].any(axis=0)
        ok = (
            np.isfinite(r)
            & (np.nan_to_num(g.volume[i]) >= min_volume)
            & (g.age[i] >= 365)
            & week
            & (sizes[i] > 0)
        )
        eligible = [j for j in range(len(g.symbols)) if ok[j]]
        if len(eligible) < 2 * top:
            return {}
        ranked = sorted(eligible, key=lambda j: (-r[j], g.symbols[j]))
        out = {g.symbols[j]: sizes[i, j] / (2 * top) for j in ranked[:top]}
        out |= {g.symbols[j]: -sizes[i, j] / (2 * top) for j in ranked[-top:]}
        return out

    return sunday_weights(g, choose)


@dataclass
class NeutralResult:
    days: int
    first: date
    last: date
    weeks: int
    mean_weekly: float
    p: float
    beta: float
    years: dict[int, float]
    """Year -> return that year."""
    max_drawdown: float
    break_even: float | None
    trades: int
    sharpe: float
    ret: float
    long_leg: float
    short_leg: float
    eligible_median: float
    deflated: float | None = None
    tests: dict[str, bool] = field(default_factory=dict)

    @property
    def passes(self) -> bool:
        return all(self.tests.values())


def neutral_result(g: Grid, w: np.ndarray, fee: float, btc_returns: np.ndarray) -> NeutralResult:
    s = simulate(g, w, fee, "perp")
    wk = weekly(s.days, s.total)
    years = np.array([d.year for d in s.days])
    gross = float(s.long_gross.sum() + s.short_gross.sum())
    costs = float(s.long_costs.sum() + s.short_costs.sum())
    sundays = [i for i, d in enumerate(g.days) if d.isoweekday() == 7]
    counts = [int((w[i] != 0).sum()) for i in sundays]
    res = NeutralResult(
        days=len(s.total),
        first=s.days[0],
        last=s.days[-1],
        weeks=len(wk),
        mean_weekly=float(wk.mean()),
        p=p_positive(wk, block=1),
        beta=beta(s.total, btc_returns),
        years={int(y): growth(s.total[years == y]) for y in YEARS if (years == y).any()},
        max_drawdown=max_drawdown(s.total),
        break_even=gross / costs if costs > 0 and gross > 0 else None,
        trades=s.long_trades + s.short_trades,
        sharpe=sharpe(s.total),
        ret=growth(s.total),
        long_leg=growth(s.long_leg),
        short_leg=growth(s.short_leg),
        eligible_median=float(np.median(counts)) if counts else 0.0,
    )
    res.tests = {
        "mean weekly > 0 (P >= 95%)": res.mean_weekly > 0 and res.p >= 0.95,
        "near market-neutral (|beta| <= 0.3)": abs(res.beta) <= 0.3,
        "positive in 4 of 6 years": sum(v > 0 for v in res.years.values()) >= 4,
        "drawdown within 50%": res.max_drawdown >= -0.5,
        "break-even cost >= 2x": (res.break_even or 0) >= 2,
        "at least 30 trades": res.trades >= 30,
    }
    return res


# ---------- cash-and-carry (note 5) ----------


@dataclass
class CarryResult:
    weeks: int
    first: date
    last: date
    mean_weekly: float
    p: float
    annual: float
    """Compounded return a year, on equity."""
    years: dict[int, float]
    max_drawdown: float
    trades: int
    held_share: dict[str, float]
    funding_by_year: dict[int, float]
    worst_week: float
    margin_calls: int
    annual_at_baseline: float
    """The same rule entering only above 0.01% a settlement (Binance's baseline rate)."""
    tests: dict[str, bool] = field(default_factory=dict)

    @property
    def passes(self) -> bool:
        return all(self.tests.values())


STABLECOIN_YIELD = 0.04
MARGIN_CALL = 1.45


def carry(
    g: Grid,
    settlements: dict[str, pl.DataFrame],
    fee: float,
    entry: float = 0.0,
    start: datetime = START,
) -> tuple[list[date], np.ndarray, int, dict[str, float], np.ndarray, int]:
    """Weekly returns on equity of note 5's rule, entering when the mean funding of the
    last 21 settlements is above ``entry``. Returns (week starts, weekly returns, trades,
    share of weeks held per coin, funding earned per week, margin calls)."""
    sundays = [i for i, d in enumerate(g.days) if d.isoweekday() == 7 and d >= start.date()]
    m = len(g.symbols)
    held = np.zeros(m, dtype=bool)
    weeks: list[date] = []
    out: list[float] = []
    earned: list[float] = []
    trades = calls = 0
    held_weeks = np.zeros(m)
    for a, b in itertools.pairwise(sundays):
        week_ret = 0.0
        week_funding = 0.0
        monday = g.days[a] + timedelta(days=1)
        for j, s in enumerate(g.symbols):
            f = settlements[s]
            cut = datetime(g.days[a].year, g.days[a].month, g.days[a].day, tzinfo=UTC) + (
                timedelta(days=1)
            )
            recent = f.filter(pl.col("time") < cut).tail(21)["rate"]
            want = recent.len() == 21 and float(recent.mean()) > entry  # type: ignore[arg-type]
            band = float(np.nan_to_num(g.band[a, j], nan=0.0015))
            trip = (fee + band) + (PERP_FEE + band)  # one side of both legs
            third = 1 / m
            hedge = 2 / 3 * third
            if want != held[j]:
                week_ret -= hedge * trip
                trades += want
            held[j] = want
            if not want:
                continue
            held_weeks[j] += 1
            lo = datetime(monday.year, monday.month, monday.day, tzinfo=UTC)
            hi = lo + timedelta(days=7)
            paid = f.filter((pl.col("time") >= lo) & (pl.col("time") < hi))["rate"].sum()
            week_ret += hedge * float(paid)
            week_funding += hedge * float(paid)
            entry_open = g.open[a + 1, j] if a + 1 < len(g.days) else np.nan
            highs = g.high[a + 1 : b + 1, j]
            if np.isfinite(entry_open) and np.nanmax(highs) >= MARGIN_CALL * entry_open:
                calls += 1
                week_ret -= hedge * 2 * trip  # both legs closed and reopened
        weeks.append(monday)
        out.append(week_ret)
        earned.append(week_funding)
    share = {s: float(held_weeks[j] / max(1, len(out))) for j, s in enumerate(g.symbols)}
    return weeks, np.array(out), trades, share, np.array(earned), calls


def carry_result(g: Grid, settlements: dict[str, pl.DataFrame], fee: float) -> CarryResult:
    weeks, r, trades, share, earned, calls = carry(g, settlements, fee)
    _, base, *_ = carry(g, settlements, fee, entry=0.0001)
    years = np.array([w.year for w in weeks])
    n = len(r)
    annual = float(np.prod(1 + r) ** (52 / n) - 1) if n else 0.0
    res = CarryResult(
        weeks=n,
        first=weeks[0],
        last=weeks[-1],
        mean_weekly=float(r.mean()),
        p=p_positive(r, block=1),
        annual=annual,
        years={int(y): growth(r[years == y]) for y in YEARS if (years == y).any()},
        max_drawdown=max_drawdown(r),
        trades=trades,
        held_share=share,
        funding_by_year={
            int(y): float(earned[years == y].sum()) for y in YEARS if (years == y).any()
        },
        worst_week=float(r.min()),
        margin_calls=calls,
        annual_at_baseline=float(np.prod(1 + base) ** (52 / len(base)) - 1) if len(base) else 0.0,
    )
    res.tests = {
        "mean weekly > 0 (P >= 95%)": res.mean_weekly > 0 and res.p >= 0.95,
        "beats a 4% stablecoin yield": res.annual > STABLECOIN_YIELD,
        "positive in 4 of 6 years": sum(v > 0 for v in res.years.values()) >= 4,
        "drawdown within 50%": res.max_drawdown >= -0.5,
        "at least 30 trades": res.trades >= 30,
    }
    return res


# ---------- report ----------


def mark(ok: bool) -> str:
    return "pass" if ok else "FAIL"


def format_leg(title: str, results: list[LegResult], need: int) -> str:
    lines = [title, ""]
    for r in results:
        lines += [
            f"{r.market}  ({r.first} to {r.last}, {r.days} days)",
            f"  Sharpe {r.sharpe:.2f} long/short vs {r.long_only_sharpe:.2f} long-only; "
            f"return {r.ret:+.0%} vs {r.long_only_ret:+.0%}; drawdown {r.max_drawdown:.0%}",
            f"  Short leg {r.short_leg_return:+.1%} (P > 0 {r.short_leg_p:.0%}), "
            f"{r.short_trades} short entries, {r.close_outs} close-outs, break-even "
            + (
                "n/a (loses before costs)"
                if r.short_break_even is None
                else f"{r.short_break_even:.1f}x costs"
            ),
            "  By year (L/S vs long-only Sharpe): "
            + ", ".join(f"{y} {a:.2f}/{b:.2f}" for y, (a, b) in r.years.items())
            + f"  -> {r.years_beaten} of {len(r.years)} beaten",
            f"  BTC beta {r.beta:.2f}; correlation with long-only {r.correlation:.2f}; "
            f"deflated Sharpe {'n/a' if r.deflated is None else f'{r.deflated:.0%}'}",
            f"  As a perp: Sharpe {r.perp_sharpe:.2f}, short leg {r.perp_short_leg_return:+.1%}; "
            f"CFD at 15% financing: Sharpe {r.stress_sharpe:.2f}",
        ]
        lines += [f"    {mark(ok)}  {name}" for name, ok in r.tests.items()]
        lines.append("")
    passed = sum(r.passes for r in results)
    verdict = "NEEDS-SHORTING" if passed >= need else "DROP"
    lines.append(f"Verdict: {verdict} ({passed} of {len(results)} pass every test; {need} needed)")
    return "\n".join(lines)


def format_neutral(r: NeutralResult) -> str:
    lines = [
        "Long/short momentum among liquid coins (note long-short-momentum-among-liquid-coins)",
        "",
        f"{r.first} to {r.last}: {r.weeks} weeks; median {r.eligible_median:.0f} positions a "
        "Sunday",
        f"  Return {r.ret:+.0%}, Sharpe {r.sharpe:.2f}, drawdown {r.max_drawdown:.0%}; "
        f"long leg {r.long_leg:+.0%}, short leg {r.short_leg:+.0%}",
        f"  Mean week {r.mean_weekly:+.2%} (P > 0 {r.p:.0%}); BTC beta {r.beta:.2f}; "
        f"{r.trades} trades; break-even "
        + ("n/a" if r.break_even is None else f"{r.break_even:.1f}x costs"),
        "  By year: " + ", ".join(f"{y} {v:+.0%}" for y, v in r.years.items()),
        f"  Deflated Sharpe {'n/a' if r.deflated is None else f'{r.deflated:.0%}'}",
    ]
    lines += [f"    {mark(ok)}  {name}" for name, ok in r.tests.items()]
    lines.append(f"\nVerdict: {'NEEDS-SHORTING' if r.passes else 'DROP'}")
    return "\n".join(lines)


def format_carry(r: CarryResult) -> str:
    lines = [
        "Cash-and-carry on BTC, ETH and SOL perps (note cash-and-carry-on-btc-eth-and-sol-perps)",
        "",
        f"{r.first} to {r.last}: {r.weeks} weeks",
        f"  {r.annual:+.1%} a year on equity (mean week {r.mean_weekly:+.3%}, P > 0 {r.p:.0%}); "
        f"drawdown {r.max_drawdown:.1%}; worst week {r.worst_week:+.2%}; {r.trades} trades; "
        f"{r.margin_calls} margin calls",
        "  By year: " + ", ".join(f"{y} {v:+.1%}" for y, v in r.years.items()),
        "  Funding earned by year: "
        + ", ".join(f"{y} {v:+.1%}" for y, v in r.funding_by_year.items()),
        "  Weeks held: " + ", ".join(f"{s} {v:.0%}" for s, v in r.held_share.items()),
        f"  Entering only above 0.01% a settlement: {r.annual_at_baseline:+.1%} a year",
    ]
    lines += [f"    {mark(ok)}  {name}" for name, ok in r.tests.items()]
    lines.append(f"\nVerdict: {'NEEDS-SHORTING' if r.passes else 'DROP'}")
    return "\n".join(lines)


# ---------- running a note ----------

NOTES = {
    "btc-regime-trend": ("shorting-with-the-btc-regime-rule", "study:short-btc-regime-trend"),
    "btc-rotation": ("g1-with-a-short-side", "study:short-btc-rotation"),
    "trend-vol-target": ("trend-vol-target-long-and-short", "study:short-trend-vol-target"),
    "long-short-momentum": (
        "long-short-momentum-among-liquid-coins",
        "study:long-short-momentum",
    ),
    "cash-and-carry": ("cash-and-carry-on-btc-eth-and-sol-perps", "study:cash-and-carry"),
}
"""Each study: the frozen note that pre-registers it and the implementation it names."""


@dataclass
class Outcome:
    name: str
    text: str
    looks: list[tuple[str, float]]
    """(market, Sharpe) per trial, for the trial log."""
    verdict: str
    detail: dict[str, object]
    results: list[object] = field(default_factory=list)
    curves: dict[str, tuple[list[date], np.ndarray]] = field(default_factory=dict)
    """Label -> (each period's end, its return), for the dashboard's chart."""


def market_returns(g: Grid, symbol: str, start: datetime = START) -> np.ndarray:
    """A coin's open-to-open daily returns on the days ``simulate`` scores."""
    j = g.symbols.index(symbol)
    w = np.zeros_like(g.open)
    w[:, j] = 1.0
    s = simulate(g, w, 0.0, "perp", start)
    return s.long_gross


def run_note(
    name: str,
    panel_for: Callable[[list[str]], pl.DataFrame],
    funding_for: Callable[[str], pl.DataFrame | None],
    tiers: Sequence[tuple[float, float]],
    fee: float,
    exploration: list[str] | None = None,
) -> Outcome:
    """Run study ``name`` (a key of NOTES). ``panel_for(coins)`` loads daily bars before
    the holdout (kestrel.data.panel.load_panel's shape); ``funding_for(coin)`` a coin's perp
    funding; ``exploration`` the exploration-half coins (note 4 only)."""
    btc = "BTCUSDT"
    if name in ("btc-regime-trend", "trend-vol-target"):
        coins = (
            ["ETHUSDT", "SOLUSDT"] if name == "btc-regime-trend" else [btc, "ETHUSDT", "SOLUSDT"]
        )
        results = []
        for coin in coins:
            symbols = sorted({coin, btc})
            g = grid(panel_for(symbols), {s: funding_for(s) for s in symbols}, tiers)
            j, b = g.symbols.index(coin), g.symbols.index(btc)
            long_only = np.zeros_like(g.open)
            long_short = np.zeros_like(g.open)
            if name == "btc-regime-trend":
                lo, ls = regime_trend_weights(g.close[:, j], g.close[:, b])
            else:
                lo, ls = trend_votes_weights(g.close[:, j])
            long_only[:, j], long_short[:, j] = lo, ls
            results.append(leg_result(coin, g, long_short, long_only, fee, market_returns(g, btc)))
        need = 2
        title = (
            "Shorting with the BTC regime rule (note shorting-with-the-btc-regime-rule)"
            if name == "btc-regime-trend"
            else "Trend-vol-target long and short (note trend-vol-target-long-and-short)"
        )
        return Outcome(
            name, format_leg(title, results, need), [(r.market, r.sharpe) for r in results],
            "NEEDS-SHORTING" if sum(r.passes for r in results) >= need else "DROP",
            {}, list(results),
        )  # fmt: skip
    if name == "btc-rotation":
        from kestrel.strategies.btc_rotation import LARGE_8

        coins = list(LARGE_8)
        g = grid(panel_for(coins), {s: funding_for(s) for s in coins}, tiers)
        b = g.symbols.index(btc)
        lo = rotation_weights(g, b, short_side=False)
        ls = rotation_weights(g, b, short_side=True)
        r = leg_result("G1's 8 coins", g, ls, lo, fee, market_returns(g, btc))
        text = format_leg("G1 with a short side (note g1-with-a-short-side)", [r], 1)
        return Outcome(name, text, [("UNIVERSE", r.sharpe)], "NEEDS-SHORTING" if r.passes
                       else "DROP", {}, [r])  # fmt: skip
    if name == "long-short-momentum":
        if exploration is None:
            raise ValueError("long/short momentum needs the exploration split")
        coins = sorted(set(exploration) | {btc, "ETHUSDT", "SOLUSDT"})
        g = grid(panel_for(coins), {s: funding_for(s) for s in coins}, tiers)
        w = long_short_momentum_weights(g)
        neutral = neutral_result(g, w, fee, market_returns(g, btc))
        return Outcome(name, format_neutral(neutral), [("UNIVERSE", neutral.sharpe)],
                       "NEEDS-SHORTING" if neutral.passes else "DROP", {}, [neutral])  # fmt: skip
    if name == "cash-and-carry":
        coins = [btc, "ETHUSDT", "SOLUSDT"]
        g = grid(panel_for(coins), {s: funding_for(s) for s in coins}, tiers)
        settlements = {}
        for s in coins:
            f = funding_for(s)
            if f is None:
                raise ValueError(f"no stored funding for {s}")
            settlements[s] = f.sort("time")
        res = carry_result(g, settlements, fee)
        weeks, weekly_r, *_ = carry(g, settlements, fee)
        sd = float(np.std(weekly_r, ddof=1))
        annual_sharpe = float(weekly_r.mean()) / sd * math.sqrt(52) if sd > 0 else 0.0
        stable = np.full(len(weekly_r), (1 + STABLECOIN_YIELD) ** (1 / 52) - 1)
        ends = [w + timedelta(days=7) for w in weeks]
        curves = {"Cash-and-carry": (ends, weekly_r), "A 4% stablecoin yield": (ends, stable)}
        return Outcome(name, format_carry(res), [("UNIVERSE", annual_sharpe)],
                       "NEEDS-SHORTING" if res.passes else "DROP", {}, [res],
                       curves)  # fmt: skip
    raise ValueError(f"no shorting study {name!r}; one of {', '.join(NOTES)}")


def save(outcome: Outcome, slug: str, data_dir: Path | str, commit: str | None) -> Path:
    """The study's report for the dashboard (``<data>/shorting/<name>.json``). A rerun of
    the same frozen note replaces it: the rule is fixed, so only the code can change it,
    and the commit is recorded."""
    tests: dict[str, bool] = {}
    for r in outcome.results:
        for k, v in getattr(r, "tests", {}).items():
            market = getattr(r, "market", None)
            tests[f"{market}: {k}" if market and len(outcome.results) > 1 else k] = bool(v)
    curves = []
    for label, (ends, r) in outcome.curves.items():
        # Start at 1 one period before the first end.
        step = ends[1] - ends[0] if len(ends) > 1 else timedelta(days=1)
        times = [ends[0] - step, *ends]
        equity = [1.0, *(float(x) for x in np.cumprod(1 + r))]
        curves.append({"label": label, "time": [d.isoformat() for d in times], "equity": equity})
    doc = {
        "name": outcome.name,
        "note": slug,
        "measured_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "verdict": outcome.verdict,
        "hypothetical": True,
        "tests": tests,
        "report": outcome.text,
        "curves": curves,
    }
    target = Path(data_dir) / "shorting" / f"{outcome.name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2))
    tmp.replace(target)
    return target


def load_all(data_dir: Path | str) -> list[dict[str, Any]]:
    """Every saved shorting study, passes first, then by name."""
    found = [json.loads(p.read_text()) for p in (Path(data_dir) / "shorting").glob("*.json")]
    return sorted(found, key=lambda d: (d["verdict"] != "NEEDS-SHORTING", d["name"]))
