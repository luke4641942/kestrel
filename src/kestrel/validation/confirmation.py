"""The exploration experiment's confirmation run (CLAUDE.md): one graduate, every coin in
the confirmation half, once.

For each confirmation coin with at least ``MIN_MONTHS`` months of daily bars before the
holdout: the strategy against holding the coin over its whole history (scorecard test 1,
on Sharpe after costs), and the fixed rule walked forward window by window (24-month
training, 6-month test windows, as everywhere else in Kestrel; with no grid, every window
judges the same rule; a coin too short for one training plus one test window adds no
windows but still counts for the first test). The pre-registered test passes when the
strategy beats holding on more than half of the coins AND beats holding in more than half
of all test windows, pooled across coins.

The whole run is one look: the confirmation pool counts it once, at the median Sharpe
across coins, however many coins it covers.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from kestrel.backtest.engine import run_backtest
from kestrel.backtest.metrics import compute_metrics
from kestrel.config import Config
from kestrel.data.binance_vision import Month
from kestrel.data.features import attach_features
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.exploration import MIN_MONTHS
from kestrel.strategy import BuyAndHold, make_strategy, spec_for
from kestrel.validation.walkforward import walk_forward

TRAIN_MONTHS, TEST_MONTHS = 24, 6
PASS_SHARE = 0.5


@dataclass(frozen=True)
class CoinResult:
    symbol: str
    sharpe: float | None
    held_sharpe: float | None
    windows: int
    windows_beating: int

    @property
    def beats_holding(self) -> bool:
        """As the walk-forward judges it: no Sharpe never beats a market that moved."""
        return self.sharpe is not None and (
            self.held_sharpe is None or self.sharpe > self.held_sharpe
        )


@dataclass(frozen=True)
class ConfirmationResult:
    strategy: str
    params: dict[str, int]
    coins: list[CoinResult]
    skipped: list[str] = field(default_factory=list)

    @property
    def beats_share(self) -> float:
        return sum(c.beats_holding for c in self.coins) / len(self.coins) if self.coins else 0.0

    @property
    def windows(self) -> int:
        return sum(c.windows for c in self.coins)

    @property
    def windows_share(self) -> float:
        return sum(c.windows_beating for c in self.coins) / self.windows if self.windows else 0.0

    @property
    def median_sharpe(self) -> float | None:
        found = [c.sharpe for c in self.coins if c.sharpe is not None]
        return statistics.median(found) if found else None

    @property
    def verdict(self) -> str:
        ok = self.beats_share > PASS_SHARE and self.windows_share > PASS_SHARE
        return "PASS" if ok else "FAIL"

    def detail(self) -> dict[str, Any]:
        return {
            "coins": len(self.coins),
            "beats_share": self.beats_share,
            "windows": self.windows,
            "windows_share": self.windows_share,
            "median_sharpe": self.median_sharpe,
            "skipped": self.skipped,
            "per_coin": [
                {
                    "symbol": c.symbol,
                    "sharpe": c.sharpe,
                    "held_sharpe": c.held_sharpe,
                    "windows": c.windows,
                    "windows_beating": c.windows_beating,
                }
                for c in self.coins
            ],
        }


def confirm(
    key: str,
    params: dict[str, int],
    coins: list[str],
    store: ParquetStore,
    config: Config,
    cash: Decimal,
) -> ConfirmationResult:
    """Run ``key`` on every coin in ``coins``; ``config`` must be unlocked for them."""
    holdout = config.backtest.holdout_start
    features = spec_for(key).features
    results: list[CoinResult] = []
    skipped: list[str] = []
    for symbol in sorted(coins):
        months = [m for m in store.months(symbol, Interval.D1) if m < f"{holdout:%Y-%m}"]
        if len(months) < MIN_MONTHS:
            skipped.append(symbol)
            continue
        bars = store.read(symbol, Interval.D1, None, holdout)
        bars = attach_features(bars, features, symbol, Interval.D1, store.root)
        mine = compute_metrics(
            run_backtest(make_strategy(key, symbol, params), bars, Interval.D1, config, cash)
        )
        held = compute_metrics(run_backtest(BuyAndHold(symbol), bars, Interval.D1, config, cash))
        try:
            wf = walk_forward(
                key,
                symbol,
                bars,
                Interval.D1,
                config,
                cash,
                {},
                start=Month.parse(months[0]),
                end=Month.parse(months[-1]),
                train_months=TRAIN_MONTHS,
                test_months=TEST_MONTHS,
                build=_builder(key, symbol, params),
            )
            windows, beating = len(wf.folds), wf.windows_beating_holding
        except ValueError:  # too short for one training plus one test window
            windows, beating = 0, 0
        results.append(
            CoinResult(
                symbol,
                mine.sharpe,
                held.sharpe,
                windows,
                beating,
            )
        )
    return ConfirmationResult(key, params, results, skipped)


def _builder(key: str, symbol: str, params: dict[str, int]) -> Callable[[dict[str, int]], Any]:
    """The walk-forward's strategy for every window: the graduate's fixed rule."""

    def build(_point: dict[str, int]) -> Any:
        return make_strategy(key, symbol, params)

    return build


def format_confirmation(r: ConfirmationResult, note: str) -> str:
    def num(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.2f}"

    ranked = sorted(r.coins, key=lambda c: (c.sharpe or -99) - (c.held_sharpe or -99), reverse=True)
    lines = [
        f"Confirmation run  ·  {r.strategy} {r.params}  ·  note {note}",
        f"{len(r.coins)} confirmation coins with {MIN_MONTHS}+ months before the holdout"
        + (f"; {len(r.skipped)} too young, left out" if r.skipped else ""),
        "",
        f"  Beats holding's Sharpe on   {sum(c.beats_holding for c in r.coins)} of "
        f"{len(r.coins)} coins ({r.beats_share:.0%}; more than 50% needed)",
        f"  Walk-forward windows        {sum(c.windows_beating for c in r.coins)} of "
        f"{r.windows} beat holding ({r.windows_share:.0%}; more than 50% needed)",
        f"  Median Sharpe across coins  {num(r.median_sharpe)}",
        "",
        "  Best and worst five against holding (Sharpe, held Sharpe, windows won):",
    ]
    for c in ranked[:5] + ranked[-5:]:
        lines.append(
            f"    {c.symbol:<14}{num(c.sharpe):>7}{num(c.held_sharpe):>7}"
            f"   {c.windows_beating}/{c.windows}"
        )
    lines += ["", f"Verdict: {r.verdict}", "One look in the confirmation pool; never rerun."]
    return "\n".join(lines)
