"""Warm-up: a backtest's strategy reads history from before its start, so it is ready on
the first day it is scored (strategy review, 2026-11-26).

Until 2026-11-26 a backtest loaded no bars before its start, so a strategy with a 120-day
lookback sat in cash for 120 days while its benchmark was invested from day 1: the
benchmark was scored on days the strategy couldn't take part in (trend-vol-target on BTC,
121 days; G1, 427 days while BTC fell 72%).

Now the strategy sees up to ``HISTORY_DAYS`` of bars before the start, read only: the
account still opens with its cash at the start, makes its first decision at the first
scored close and trades from the next open, exactly as the benchmark does. Nothing is
carried in and nothing is rebased, so minimum orders and costs are those of a fresh
account. A live account started that day would do the same: read history, then trade.

A coin listed after the start has no history to read, so its scored period begins once
the strategy's own lookback has filled (``StrategySpec.warmup``, in days): the strategy
and holding are then scored over the same days, from ``scored_from``. A strategy that
declares no warm-up is scored from the coin's first bar, as before.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl

from kestrel.strategy import BracketStrategy, PortfolioStrategy, Strategy, spec_for

HISTORY_DAYS = 400
"""Days of bars read before a backtest's start: more than any declared warm-up (the
longest is a year: G1's history rule, and slow_days' upper bound)."""


def warmup_days(key: str, params: dict[str, int]) -> int:
    """Days of history ``key`` with ``params`` needs before its first decision."""
    rule = spec_for(key).warmup
    days = rule(params) if rule else 0
    if days > HISTORY_DAYS:
        raise ValueError(f"{key} needs {days} days of warm-up; at most {HISTORY_DAYS} are read")
    return days


def history_start(start: datetime) -> datetime:
    return start - timedelta(days=HISTORY_DAYS)


def scored_from(first: datetime, start: datetime, days: int) -> datetime:
    """The first scored bar: the run's start, or later on a coin whose data begins too late
    to fill the strategy's lookback by then."""
    return max(start, first + timedelta(days=days))


def split(frame: pl.DataFrame, at: datetime) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(history before ``at``, bars from ``at``)."""
    before = pl.col("open_time") < at
    return frame.filter(before), frame.filter(~before)


class WarmedWeights:
    """A weight strategy that also reads ``history`` (bars before the engine's first)."""

    def __init__(self, inner: Strategy, history: pl.DataFrame) -> None:
        self.inner, self.history = inner, history
        self.name, self.symbol = inner.name, inner.symbol

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        weights = self.inner.target_weights(pl.concat([self.history, bars]))
        return weights.tail(bars.height)


class WarmedBrackets:
    """A bracket strategy that also reads ``history``. An entry signalled on a history bar
    is never traded: the account opens at the first scored bar."""

    def __init__(self, inner: BracketStrategy, history: pl.DataFrame) -> None:
        self.inner, self.history = inner, history
        self.name, self.symbol = inner.name, inner.symbol

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        return self.inner.brackets(pl.concat([self.history, bars])).tail(bars.height)


class WarmedPortfolio:
    """A portfolio strategy that also reads ``history`` (panel rows before the engine's
    first); decisions are kept from the scored panel's first bar on."""

    def __init__(self, inner: PortfolioStrategy, history: pl.DataFrame) -> None:
        self.inner, self.history = inner, history
        self.name = inner.name

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        if panel.is_empty():
            return self.inner.target_weights(panel)
        first = panel["open_time"].min()
        decisions = self.inner.target_weights(pl.concat([self.history, panel]))
        return decisions.filter(pl.col("open_time") >= first)


def warmed(
    strategy: Strategy | BracketStrategy, history: pl.DataFrame
) -> Strategy | BracketStrategy:
    """``strategy`` reading ``history`` too; itself when there is none."""
    if history.is_empty():
        return strategy
    if isinstance(strategy, BracketStrategy):
        return WarmedBrackets(strategy, history)
    return WarmedWeights(strategy, history)


def warmed_portfolio(strategy: PortfolioStrategy, history: pl.DataFrame) -> PortfolioStrategy:
    return strategy if history.is_empty() else WarmedPortfolio(strategy, history)
