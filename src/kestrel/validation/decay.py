"""The decay monitor: has a paper account's edge gone?

An edge found in history can fade (others find it, the market changes). Rather than judge
that by eye, each paper account is checked against a rule fixed before there was data to
tempt anyone (Luke, 2026-11-22):

- After at least ``MIN_DAYS`` paper days, take the account's model replay (the strategy at
  real Binance prices, which the notes judge it on) and its annualised Sharpe so far.
- Take the same strategy, settings and market over ``REFERENCE_START`` to the holdout (its
  research backtest), and resample its daily returns into paths of the same length with a
  circular block bootstrap (blocks of about n^(1/3) days, as kestrel.backtest.analysis).
- Flag the account if its Sharpe is below the ``PERCENTILE``th percentile of those paths:
  a run that bad would happen less than 1 time in 20 if the backtest were still true.

The flag is information. It never stops an account or changes a decision: a downgrade
waits for Luke (docs/rules.md). Before ``MIN_DAYS`` it says "too early" and costs nothing.
One flag on noisy data is a prompt to look, not a verdict; and it tests the strategy
against its own past, not against holding, which the paper note's own rules still do.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Literal

import polars as pl

from kestrel.backtest.engine import run_backtest
from kestrel.backtest.metrics import daily_equity
from kestrel.backtest.portfolio import run_portfolio
from kestrel.backtest.runs import coin_bars, universe_bars, warm_split
from kestrel.backtest.warmup import warmed, warmed_portfolio
from kestrel.config import Config
from kestrel.data.binance_vision import Month
from kestrel.data.store import ParquetStore
from kestrel.execution.paper import Terms
from kestrel.execution.tracking import Tracking
from kestrel.strategy import make_portfolio_strategy, make_strategy, spec_for

MIN_DAYS = 90
PERCENTILE = 5
REFERENCE_START = datetime(2020, 1, 1, tzinfo=UTC)
RESAMPLES = 2000
SEED = 20261122
DAYS_PER_YEAR = 365

Status = Literal["too early", "in line", "flagged"]


def sharpe(returns: Sequence[float]) -> float:
    """Annualised Sharpe of daily returns; 0 when nothing moved (a run all in cash)."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
    return mean / sd * math.sqrt(DAYS_PER_YEAR) if sd > 0 else 0.0


def sharpe_band(
    reference: Sequence[float], days: int, *, resamples: int = RESAMPLES, seed: int = SEED
) -> tuple[float, float]:
    """(the PERCENTILEth percentile, the median) of the Sharpe over ``days``-day paths
    resampled from ``reference`` in circular blocks."""
    n = len(reference)
    if n < 2 or days < 2:
        raise ValueError("the reference and the paths need at least 2 days")
    block = max(1, round(n ** (1 / 3)))
    rng = random.Random(seed)
    found: list[float] = []
    for _ in range(resamples):
        path: list[float] = []
        while len(path) < days:
            start = rng.randrange(n)
            path.extend(reference[(start + i) % n] for i in range(block))
        found.append(sharpe(path[:days]))
    found.sort()

    def at(q: float) -> float:
        return found[min(len(found) - 1, int(q / 100 * len(found)))]

    return at(PERCENTILE), at(50)


@dataclass(frozen=True)
class Decay:
    days: int
    """Daily returns of the model replay so far."""
    sharpe: float | None
    """Its annualised Sharpe (None before MIN_DAYS: not computed)."""
    floor: float | None
    """The PERCENTILEth percentile of the backtest's Sharpe over as many days."""
    median: float | None

    @property
    def status(self) -> Status:
        if self.sharpe is None or self.floor is None:
            return "too early"
        return "flagged" if self.sharpe < self.floor else "in line"


def model_returns(t: Tracking) -> list[float]:
    """The model replay's daily returns, from its marks."""
    equity = [float(d.expected) for d in t.days]
    return [b / a - 1 for a, b in pairwise(equity) if a > 0]


def assess(paper: Sequence[float], reference: Callable[[], Sequence[float]]) -> Decay:
    """The rule. ``reference`` is only called once there are MIN_DAYS paper days."""
    days = len(paper)
    if days < MIN_DAYS:
        return Decay(days, None, None, None)
    floor, median = sharpe_band(reference(), days)
    return Decay(days, sharpe(paper), floor, median)


def reference_returns(terms: Terms, data_dir: Path | str, config: Config) -> list[float]:
    """The account's strategy, settings and market from REFERENCE_START to the holdout,
    as daily returns: its research backtest, at the account's cash."""
    store = ParquetStore(data_dir)
    end = config.backtest.holdout_start
    cash = Decimal(terms.initial_cash)
    spec = spec_for(terms.strategy)
    first = Month(REFERENCE_START.year, REFERENCE_START.month)
    last = Month(end.year, end.month - 1) if end.month > 1 else Month(end.year - 1, 12)
    # Warmed on the history before REFERENCE_START, like every backtest (findings 33).
    if spec.universe:
        loaded = universe_bars(store, config, terms.strategy, terms.interval, first, last)
        history, panel = warm_split(loaded, terms.strategy, terms.params, first)
        result = run_portfolio(
            warmed_portfolio(make_portfolio_strategy(terms.strategy, terms.params), history),
            panel.filter(pl.col("open_time") < end),
            terms.interval,
            config,
            cash,
        )
    else:
        loaded = coin_bars(store, terms.strategy, terms.symbol, terms.interval, first, last)
        history, bars = warm_split(loaded, terms.strategy, terms.params, first)
        result = run_backtest(
            warmed(make_strategy(terms.strategy, terms.symbol, terms.params), history),
            bars.filter(pl.col("open_time") < end),
            terms.interval,
            config,
            cash,
        )
    equity = daily_equity(result)["equity"].to_list()
    return [b / a - 1 for a, b in pairwise(equity) if a > 0]


def format_decay(d: Decay) -> str:
    if d.status == "too early":
        return f"Decay check: too early ({d.days} of {MIN_DAYS} days)"
    assert d.sharpe is not None and d.floor is not None and d.median is not None  # noqa: PT018
    verdict = (
        "FLAGGED: below what the backtest gives 19 times in 20; a prompt to look, not a verdict"
        if d.status == "flagged"
        else "in line with the backtest"
    )
    return (
        f"Decay check over {d.days} days: Sharpe {d.sharpe:.2f} against the backtest's "
        f"{PERCENTILE}th percentile {d.floor:.2f} (median {d.median:.2f}): {verdict}"
    )
