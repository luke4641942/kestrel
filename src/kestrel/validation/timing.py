"""The random-timing control: is a weight strategy's timing better than chance?

The weight-strategy counterpart of the random-entry control (test 9, brackets only). It keeps
everything about the strategy except *when* it holds what it holds: the strategy's own
weights, rotated in time by a random offset. A rotation keeps the exposure, the length of
every holding, the number of trades and the sizing pattern exactly (one seam where the end
joins the start aside); only the alignment with the market is lost. Each copy runs through
the same engine, so it pays the same costs.

Stricter than test 1's exposure-matched holding, which pays no trading costs: a copy pays
the strategy's own turnover. The strategy's Sharpe after costs is compared with every
copy's; it beats random timing when it beats at least ``BEAT_SHARE`` of them.

The warm-up (the bars before the strategy first holds anything) is left in cash and only
the stretch after it is rotated. Offsets are at least a tenth of that stretch, so a copy is
never the strategy shifted by a few days. Seeded, so a rerun gives the same answer. Copies
aren't settings chosen from the data, so they don't count as trials. Information only (Luke,
2026-11-26): it is not a scorecard test.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import polars as pl

from kestrel.backtest.engine import run_backtest
from kestrel.backtest.metrics import compute_metrics
from kestrel.config import Config
from kestrel.data.schema import Interval
from kestrel.strategy import Strategy, validated_weights
from kestrel.validation.rules import RANDOM_BEAT_SHARE as BEAT_SHARE

PATHS = 200
SEED = 20261126
MIN_OFFSET_SHARE = 0.1
"""Smallest rotation, as a share of the rotated stretch."""


class Rotated:
    """Replays a fixed weight series (prefix-consistent: a shorter history gets its head)."""

    def __init__(self, name: str, symbol: str, weights: list[float]) -> None:
        self.name, self.symbol, self.weights = name, symbol, weights

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        return pl.Series("weight", self.weights[: bars.height], dtype=pl.Float64)


def rotate(weights: list[float], offset: int) -> list[float]:
    """The weights after the warm-up (before the first non-zero weight), rotated right by
    ``offset``; the warm-up stays as it is."""
    first = next((i for i, w in enumerate(weights) if w > 0), len(weights))
    active = weights[first:]
    if not active:
        return list(weights)
    k = offset % len(active)
    return weights[:first] + active[-k:] + active[:-k] if k else list(weights)


def offsets(weights: list[float], paths: int, rng: random.Random) -> list[int]:
    """``paths`` distinct rotations, each at least ``MIN_OFFSET_SHARE`` of the stretch away
    from the strategy itself (either way round)."""
    first = next((i for i, w in enumerate(weights) if w > 0), len(weights))
    m = len(weights) - first
    gap = max(1, round(m * MIN_OFFSET_SHARE))
    pool = range(gap, m - gap + 1)
    if len(pool) < 1:
        raise ValueError("the strategy holds too little of the history to rotate")
    return rng.sample(pool, min(paths, len(pool)))


@dataclass(frozen=True)
class TimingControl:
    strategy: str
    sharpe: float
    exposure: float
    trades: int
    paths: list[float]
    """Each rotated copy's Sharpe after costs."""

    @property
    def beaten_share(self) -> float:
        return sum(self.sharpe > p for p in self.paths) / len(self.paths)

    def quantile(self, q: float) -> float:
        ordered = sorted(self.paths)
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    @property
    def verdict(self) -> str:
        return "BEATS RANDOM TIMING" if self.beaten_share >= BEAT_SHARE else "NO BETTER THAN RANDOM"


def run_timing_control(
    strategy: Strategy,
    bars: pl.DataFrame,
    interval: Interval,
    config: Config,
    cash: Decimal,
    paths: int = PATHS,
    seed: int = SEED,
    on_path: Callable[[int], None] = lambda _: None,
) -> TimingControl:
    """The strategy once, then ``paths`` rotated copies of its weights on the same bars."""
    own = compute_metrics(run_backtest(strategy, bars, interval, config, cash))
    if own.sharpe is None:
        raise ValueError(f"{strategy.name} has no Sharpe on this data: nothing to compare")
    weights = [float(w) for w in validated_weights(strategy, bars)]
    sharpes: list[float] = []
    for i, k in enumerate(offsets(weights, paths, random.Random(seed))):
        copy = Rotated(f"{strategy.name}, rotated {k} bars", strategy.symbol, rotate(weights, k))
        m = compute_metrics(run_backtest(copy, bars, interval, config, cash))
        sharpes.append(m.sharpe if m.sharpe is not None else 0.0)
        on_path(i + 1)
    return TimingControl(strategy.name, own.sharpe, own.exposure, own.trades, sharpes)


def format_timing(c: TimingControl, market: str) -> str:
    return "\n".join(
        [
            f"Random-timing control  ·  {c.strategy}  ·  {market}",
            "",
            f"  Strategy          Sharpe {c.sharpe:.2f} after costs, exposure {c.exposure:.0%}, "
            f"{c.trades} trades",
            f"  Rotated copies    median {c.quantile(0.5):.2f}, 95th percentile "
            f"{c.quantile(0.95):.2f} ({len(c.paths)} copies)",
            f"  Beats             {c.beaten_share:.0%} of copies ({BEAT_SHARE:.0%} to say it "
            "beats random timing)",
            "",
            f"  Verdict: {c.verdict}",
            "  Same weights, exposure, holding lengths and costs; only the timing is random.",
            "  Information only: not a scorecard test.",
        ]
    )
