"""The holdout test: one look at the reserved year, for a finished strategy.

Data from ``backtest.holdout_start`` (2025-11-01) was set aside before any research, for one
final verdict on a strategy with nothing left to tune. This is the only code path that may
read it, and only for a frozen research note that names the test in advance.

The strategy runs from a warm-up month before the holdout, so a 120-day trend is already
voting on the first holdout day; only the days from ``start`` to ``end`` are scored. A
position held at ``start`` carries in at that day's mark, as in walk-forward. Holding the
same coin over the same days is the comparison.

The edge rule, fixed in the notes before the holdout was looked at: if holding's Sharpe
over the year is positive, the strategy's Sharpe must be at least holding's. In a falling
year (holding's Sharpe zero or negative), comparing Sharpes would score a strategy that
lost far less as worse (lower volatility makes a negative Sharpe more negative), so the
strategy's return must instead beat holding at the strategy's average exposure. The worst
drawdown inside the year must also be no deeper than 50%.

A coin-basket strategy is scored the same way, with its own run (the portfolio engine on
the universe panel) against BTC held, the benchmark its backtests use: see ``score``.

Each note may use the holdout once: the record in ``<data>/holdout/<note>.json`` is
written before the result is shown and refuses a second run. Holdout runs are never
trials (they would leak the holdout into the deflated Sharpe) and never reach the API.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from kestrel.backtest.engine import BacktestResult, run_backtest
from kestrel.backtest.metrics import daily_equity
from kestrel.config import Config
from kestrel.data.schema import Interval
from kestrel.strategy import BuyAndHold, make_strategy
from kestrel.validation import rules
from kestrel.validation.walkforward import _sharpe, window_returns


@dataclass(frozen=True)
class Scored:
    sharpe: float | None
    total_return: float
    max_drawdown: float


def _score(returns: list[float]) -> Scored:
    equity, peak, worst = 1.0, 1.0, 0.0
    for r in returns:
        equity *= 1 + r
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return Scored(_sharpe(returns), equity - 1, worst)


@dataclass(frozen=True)
class HoldoutResult:
    strategy: str
    symbol: str
    params: dict[str, int]
    start: datetime
    end: datetime
    """Exclusive."""
    exposure: float
    """Average share of equity held over the scored days."""
    mine: Scored
    held: Scored
    matched_return: float
    """Holding at the strategy's average exposure: its daily returns scaled by it."""

    @property
    def falling_year(self) -> bool:
        return self.held.sharpe is None or self.held.sharpe <= 0

    @property
    def edge_passes(self) -> bool:
        if self.falling_year:
            return self.mine.total_return > self.matched_return
        return self.mine.sharpe is not None and self.mine.sharpe >= (self.held.sharpe or 0.0)

    @property
    def drawdown_passes(self) -> bool:
        return self.mine.max_drawdown >= -rules.MAX_DRAWDOWN

    @property
    def passes(self) -> bool:
        return self.edge_passes and self.drawdown_passes


def holdout_test(
    key: str,
    symbol: str,
    bars: pl.DataFrame,
    interval: Interval,
    config: Config,
    cash: Decimal,
    params: dict[str, int],
    *,
    start: datetime,
    end: datetime,
) -> HoldoutResult:
    """Run ``key`` over ``bars`` (warm-up included) and score [start, end) against holding."""
    if start < config.backtest.holdout_start:
        raise ValueError(
            "a holdout test scores the holdout only "
            f"(from {config.backtest.holdout_start:%Y-%m-%d})"
        )
    if bars.height < 2 or bars["open_time"].min() >= start:  # type: ignore[operator]
        raise ValueError("the bars must start before the scored period, to warm the strategy up")
    mine = run_backtest(
        make_strategy(key, symbol, params), bars, interval, config, cash, allow_holdout=True
    )
    held = run_backtest(BuyAndHold(symbol), bars, interval, config, cash, allow_holdout=True)
    return score(key, symbol, params, mine, held, start=start, end=end)


def score(
    key: str,
    symbol: str,
    params: dict[str, int],
    mine: BacktestResult,
    held: BacktestResult,
    *,
    start: datetime,
    end: datetime,
) -> HoldoutResult:
    """Score a strategy's run and the benchmark's over [start, end), both warmed up first."""
    my_returns = window_returns(daily_equity(mine), start, end)
    held_returns = window_returns(daily_equity(held), start, end)
    if len(my_returns) < 2:
        raise ValueError("no stored bars in the scored period")
    weights = mine.equity.filter((pl.col("close_time") > start) & (pl.col("close_time") <= end))
    exposure = float(weights["weight"].mean() or 0.0)  # type: ignore[arg-type]
    matched = math.prod(1 + exposure * r for r in held_returns) - 1
    return HoldoutResult(
        key, symbol, params, start, end, exposure, _score(my_returns), _score(held_returns), matched
    )


class HoldoutLog:
    """Every holdout test ever run, one per research note: ``<data>/holdout/<note>.json``."""

    def __init__(self, data_dir: Path | str) -> None:
        self.root = Path(data_dir) / "holdout"

    def used(self, slug: str) -> bool:
        return (self.root / f"{slug}.json").exists()

    def record(
        self, slug: str, version: int, result: HoldoutResult, warmup_start: datetime
    ) -> None:
        if self.used(slug):
            raise ValueError(f"{slug} has already used the holdout; it is used once")
        self.root.mkdir(parents=True, exist_ok=True)
        doc: dict[str, Any] = {
            "research": slug,
            "version": version,
            "recorded_at": datetime.now(UTC).isoformat(),
            "warmup_start": warmup_start.isoformat(),
            **asdict(result),
            "falling_year": result.falling_year,
            "edge_passes": result.edge_passes,
            "drawdown_passes": result.drawdown_passes,
            "passes": result.passes,
        }
        tmp = self.root / f".{slug}.tmp"
        tmp.write_text(json.dumps(doc, indent=2, default=str))
        tmp.replace(self.root / f"{slug}.json")

    def records(self) -> list[dict[str, Any]]:
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("*.json"))]


def format_holdout(r: HoldoutResult) -> str:
    def num(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.2f}"

    edge = (
        f"return {r.mine.total_return:+.1%} vs {r.matched_return:+.1%} for holding at "
        f"{r.exposure:.0%} exposure (a falling year: returns are compared)"
        if r.falling_year
        else f"Sharpe {num(r.mine.sharpe)} vs {num(r.held.sharpe)} for holding"
    )
    return "\n".join(
        [
            f"HOLDOUT TEST  ·  {r.strategy}  ·  {r.symbol}  ·  {r.start:%Y-%m-%d} to "
            f"{r.end:%Y-%m-%d} (exclusive)",
            "",
            f"  {'':<14} {'strategy':>10} {'holding':>10}",
            f"  {'Return':<14} {r.mine.total_return:>+10.1%} {r.held.total_return:>+10.1%}",
            f"  {'Sharpe':<14} {num(r.mine.sharpe):>10} {num(r.held.sharpe):>10}",
            f"  {'Worst drawdown':<14} {r.mine.max_drawdown:>10.1%} {r.held.max_drawdown:>10.1%}",
            f"  {'Exposure':<14} {r.exposure:>10.0%}",
            "",
            f"  Edge:      {'PASS' if r.edge_passes else 'FAIL'}  {edge}",
            f"  Drawdown:  {'PASS' if r.drawdown_passes else 'FAIL'}  "
            f"worst {r.mine.max_drawdown:.1%} (limit -{rules.MAX_DRAWDOWN:.0%})",
            "",
            "  Verdict: "
            + (
                "PASSES the holdout test (support for paper trading, not proof)."
                if r.passes
                else "FAILS the holdout test."
            ),
        ]
    )
