"""Walk-forward testing: choose parameters on one window, judge them on the next.

A backtest's parameters are usually chosen on the same data that then judges them, so
its Sharpe is flattered. Walk-forward separates the two. With rolling windows of
``train_months`` and ``test_months``:

    fold 1:  [ train: choose the best grid point by Sharpe ][ test: run it, unseen ]
    fold 2:            [ train                             ][ test                  ]
    ...

Each fold's test window starts where its training window ends, and test windows follow
one another, so joining them gives one out-of-sample record. It is compared with the
in-sample Sharpe of the chosen points: the strategy backlog's kill rule is an
out-of-sample Sharpe below half the in-sample one. Each test window is also compared with
holding the coin over the same days: the scorecard (kestrel.validation.scorecard) asks that
most windows beat it on Sharpe. Sharpe doesn't change with position size, so beating
holding's Sharpe is the same as beating the coin held at the strategy's exposure.

The chosen strategy runs on the training and test bars together, so its indicators are
warm when the test window opens; only the test window's daily returns count. A position
it already holds at the window's start carries in at that day's mark (its entry cost
fell in training).

Coin baskets. A strategy that ranks the coin universe passes ``build``, ``run`` and
``hold``: each window's panel runs through the portfolio engine, and it is compared with
holding BTC (kestrel.backtest.runs.UNIVERSE_BENCHMARK), the benchmark its backtest and
scorecard already use. One-coin strategies use the defaults: the coin itself, held.

Every grid point is a trial: each is saved with its average training Sharpe
(kestrel.validation.log) and counted by the deflated Sharpe. With no grid, nothing is
chosen: the fixed rule is judged window by window (one look).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import polars as pl

from kestrel.backtest.engine import BacktestResult, run_backtest
from kestrel.backtest.metrics import DAYS_PER_YEAR, compute_metrics, daily_equity
from kestrel.config import Config
from kestrel.data.binance_vision import Month
from kestrel.data.schema import Interval
from kestrel.strategy import BuyAndHold, make_strategy


@dataclass(frozen=True)
class Fold:
    train_start: datetime
    test_start: datetime
    test_end: datetime
    """Exclusive."""
    chosen: dict[str, int]
    train_sharpe: float
    test_returns: list[float]
    """Daily returns inside the test window."""
    benchmark_returns: list[float]

    @property
    def test_return(self) -> float:
        return math.prod(1 + r for r in self.test_returns) - 1

    @property
    def test_sharpe(self) -> float | None:
        return _sharpe(self.test_returns)

    @property
    def benchmark_sharpe(self) -> float | None:
        return _sharpe(self.benchmark_returns)

    @property
    def beats_holding(self) -> bool:
        """A better Sharpe than holding over the test window. No Sharpe (never invested,
        or flat) never beats a market that moved; a flat market is beaten by any Sharpe."""
        mine, held = self.test_sharpe, self.benchmark_sharpe
        return mine is not None and (held is None or mine > held)


@dataclass(frozen=True)
class WalkForward:
    strategy: str
    symbol: str
    interval: Interval
    grid: dict[str, list[int]]
    folds: list[Fold]
    looks: list[tuple[dict[str, int], float]]
    """Every grid point tried, with its average training Sharpe across folds: each is a
    look at the data, saved as a trial (kestrel.validation.log)."""

    @property
    def configurations(self) -> int:
        return math.prod(len(v) for v in self.grid.values())

    @property
    def oos_returns(self) -> list[float]:
        return [r for f in self.folds for r in f.test_returns]

    @property
    def oos_sharpe(self) -> float | None:
        return _sharpe(self.oos_returns)

    @property
    def oos_return(self) -> float:
        return math.prod(1 + r for r in self.oos_returns) - 1

    @property
    def benchmark_return(self) -> float:
        return math.prod(1 + r for f in self.folds for r in f.benchmark_returns) - 1

    @property
    def benchmark_sharpe(self) -> float | None:
        """Holding's Sharpe over the joined test windows."""
        return _sharpe([r for f in self.folds for r in f.benchmark_returns])

    @property
    def windows_beating_holding(self) -> int:
        return sum(f.beats_holding for f in self.folds)

    @property
    def is_sharpe(self) -> float:
        """Average in-sample Sharpe of the points chosen."""
        return sum(f.train_sharpe for f in self.folds) / len(self.folds)

    @property
    def ratio(self) -> float | None:
        """Out-of-sample Sharpe over in-sample; None unless in-sample is positive."""
        oos = self.oos_sharpe
        return oos / self.is_sharpe if oos is not None and self.is_sharpe > 0 else None

    @property
    def survives(self) -> bool:
        """The backlog's rule: out-of-sample Sharpe at least half the in-sample one."""
        return self.ratio is not None and self.ratio >= 0.5


def _sharpe(returns: list[float]) -> float | None:
    n = len(returns)
    if n < 2:
        return None
    mean = sum(returns) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
    return mean / sd * math.sqrt(DAYS_PER_YEAR) if sd > 0 else None


def _month_start(m: Month) -> datetime:
    return datetime(m.year, m.month, 1, tzinfo=UTC)


def _add(m: Month, months: int) -> Month:
    index = m.year * 12 + m.month - 1 + months
    return Month(index // 12, index % 12 + 1)


def window_returns(equity: pl.DataFrame, start: datetime, end: datetime) -> list[float]:
    """Daily returns from the mark at ``start`` to the mark at ``end``."""
    marks = equity.filter((pl.col("close_time") >= start) & (pl.col("close_time") <= end))
    return marks["equity"].pct_change().drop_nulls().to_list()


def walk_forward(
    key: str,
    symbol: str,
    bars: pl.DataFrame,
    interval: Interval,
    config: Config,
    cash: Decimal,
    grid: dict[str, list[int]],
    *,
    start: Month,
    end: Month,
    train_months: int,
    test_months: int,
    build: Callable[[dict[str, int]], Any] | None = None,
    run: Callable[[Any, pl.DataFrame], BacktestResult] | None = None,
    hold: Callable[[datetime, datetime], BacktestResult] | None = None,
) -> WalkForward:
    """Walk ``key`` forward over ``bars`` from ``start`` to ``end`` (inclusive months).

    ``build`` makes the strategy for a grid point (a ValueError skips an impossible one),
    ``run`` backtests it on a window's bars, and ``hold`` is the benchmark from one instant
    to another. The defaults trade and hold ``symbol`` alone."""
    if build is None:
        build = lambda point: make_strategy(key, symbol, point)  # noqa: E731
    if run is None:
        run = lambda strategy, frame: run_backtest(strategy, frame, interval, config, cash)  # noqa: E731
    if hold is None:

        def hold(t0: datetime, t2: datetime) -> BacktestResult:
            window = bars.filter((pl.col("open_time") >= t0) & (pl.col("open_time") < t2))
            return run_backtest(BuyAndHold(symbol), window, interval, config, cash)

    if train_months < 1 or test_months < 1:
        raise ValueError("train and test windows must be at least a month")
    # No grid: nothing is chosen, and each test window simply judges the fixed rule (its
    # defaults, or a strategy with no settings at all).
    points = [dict(zip(grid, values, strict=True)) for values in itertools.product(*grid.values())]
    stop = _month_start(end.next())
    folds: list[Fold] = []
    tried: dict[tuple[tuple[str, int], ...], list[float]] = {}
    train = start
    while _month_start(_add(train, train_months + test_months)) <= stop:
        t0 = _month_start(train)
        t1 = _month_start(_add(train, train_months))
        t2 = _month_start(_add(train, train_months + test_months))
        train_bars = bars.filter((pl.col("open_time") >= t0) & (pl.col("open_time") < t1))
        both = bars.filter((pl.col("open_time") >= t0) & (pl.col("open_time") < t2))

        best: tuple[float, dict[str, int]] | None = None
        for point in points:
            try:
                strategy = build(point)
            except ValueError:
                continue  # an impossible combination, e.g. fast >= slow
            sharpe = compute_metrics(run(strategy, train_bars)).sharpe
            if math.isnan(sharpe):
                if len(points) == 1:  # a fixed rule is tested even if training can't score it
                    best = (sharpe, point)
                continue
            tried.setdefault(tuple(point.items()), []).append(sharpe)
            if best is None or sharpe > best[0]:
                best = (sharpe, point)
        if best is None:
            raise ValueError(f"no grid point produced a Sharpe in the window from {train}")

        tested = daily_equity(run(build(best[1]), both))
        held = daily_equity(hold(t0, t2))
        folds.append(
            Fold(
                train_start=t0,
                test_start=t1,
                test_end=t2,
                chosen=best[1],
                train_sharpe=best[0],
                test_returns=window_returns(tested, t1, t2),
                benchmark_returns=window_returns(held, t1, t2),
            )
        )
        train = _add(train, test_months)
    if not folds:
        raise ValueError("the range is shorter than one training plus one test window")
    looks = [(dict(point), sum(v) / len(v)) for point, v in tried.items()]
    return WalkForward(key, symbol, interval, grid, folds, looks)


def format_walk_forward(wf: WalkForward) -> str:
    def pct(x: float) -> str:
        return f"{x:+.1%}"

    def num(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.2f}"

    lines = [
        f"Walk-forward  ·  {wf.strategy}  ·  {wf.symbol} {wf.interval.value}  ·  "
        f"{len(wf.folds)} folds, {wf.configurations} grid points each",
        "",
        f"  {'test window':<23} {'chosen':<32} {'train SR':>8} {'test':>8} {'holding':>8}"
        f" {'test SR':>8} {'held SR':>8}",
    ]
    for f in wf.folds:
        chosen = " ".join(f"{k}={v}" for k, v in f.chosen.items())
        held = math.prod(1 + r for r in f.benchmark_returns) - 1
        lines.append(
            f"  {f.test_start:%Y-%m-%d} to {f.test_end:%Y-%m-%d}  {chosen:<32} "
            f"{f.train_sharpe:>8.2f} {pct(f.test_return):>8} {pct(held):>8}"
            f" {num(f.test_sharpe):>8} {num(f.benchmark_sharpe):>8}"
        )
    ratio = wf.ratio
    lines += [
        "",
        f"  In-sample Sharpe (chosen points)  {num(wf.is_sharpe)}",
        f"  Out-of-sample Sharpe              {num(wf.oos_sharpe)}"
        + (f"   ({ratio:.0%} of in-sample)" if ratio is not None else ""),
        f"  Out-of-sample return              {pct(wf.oos_return)}   "
        f"holding: {pct(wf.benchmark_return)}",
        f"  Beats holding's Sharpe in         {wf.windows_beating_holding} of "
        f"{len(wf.folds)} test windows   (holding's Sharpe {num(wf.benchmark_sharpe)})",
        "",
        "  Verdict: "
        + (
            "holds up out of sample (at least half the in-sample Sharpe)."
            if wf.survives
            else "KILL: the out-of-sample Sharpe is under half the in-sample one."
        ),
        f"  Every grid point is a trial: {len(wf.looks)} saved to the trial log.",
    ]
    return "\n".join(lines)
