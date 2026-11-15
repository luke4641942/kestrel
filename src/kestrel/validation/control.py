"""The random-entry control: is a bracket strategy's edge more than its payoff shape?

A bracket that wins 4R and loses 1R looks good on its best days whatever picks the entry.
The control keeps everything about the strategy except *when* it enters: the same number
of entry signals, at bars drawn at random from the stretch where the strategy itself
signalled, each given one of the strategy's own stop and target distances (as fractions
of the signal close, or of the limit price for a limit entry; shuffled), entered at the
next open. The same engine then sizes, fills and costs them exactly
as it does the strategy. Long only, like every Kestrel strategy, so only the entry time
is random. Exit signals are not copied: a random trade ends at its stop, its target or
the end of the data.

Each path is one such random strategy. The strategy's net expectancy per trade (R, after
costs) is compared with every path's; it passes when it beats at least ``BEAT_SHARE`` of
them. A random strategy with no edge should lose about its costs a trade, so a strategy
that can't beat this has shown nothing beyond its stops and targets.

Paths are seeded, so a rerun gives the same answer. Paths aren't parameter settings
chosen from the data, so they don't count as trials.
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
from kestrel.strategy import BracketStrategy
from kestrel.validation.rules import RANDOM_BEAT_SHARE as BEAT_SHARE

PATHS = 200
SEED = 20261115


@dataclass(frozen=True)
class Bracket:
    """A stop and target as fractions of the signal close."""

    stop: float
    """Below the close: stop = close x (1 - stop)."""
    target: float | None
    """Above the close, or None for no target."""


def brackets_of(frame: pl.DataFrame, bars: pl.DataFrame) -> tuple[list[int], list[Bracket]]:
    """The rows a strategy signals an entry on, and each one's bracket."""
    rows: list[int] = []
    shapes: list[Bracket] = []
    closes = bars["close"].to_list()
    limits = frame["limit"] if "limit" in frame.columns else [None] * frame.height
    for i, (enter, stop, target, limit) in enumerate(
        zip(frame["enter"], frame["stop"], frame["target"], limits, strict=True)
    ):
        if enter and stop is not None:
            ref = closes[i] if limit is None else limit  # a limit's levels are from its price
            rows.append(i)
            shapes.append(Bracket(1 - stop / ref, None if target is None else target / ref - 1))
    return rows, shapes


class RandomEntries:
    """A bracket strategy entering at fixed random rows with the given brackets."""

    def __init__(self, name: str, symbol: str, entries: dict[int, Bracket]) -> None:
        self.name, self.symbol, self.entries = name, symbol, entries

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        closes = bars["close"].to_list()
        n = len(closes)
        enter = [False] * n
        stop: list[float | None] = [None] * n
        target: list[float | None] = [None] * n
        for i, b in self.entries.items():
            if i < n:  # rows past a truncated history aren't known yet
                enter[i] = True
                stop[i] = closes[i] * (1 - b.stop)
                target[i] = None if b.target is None else closes[i] * (1 + b.target)
        return pl.DataFrame(
            {"enter": enter, "stop": stop, "target": target},
            schema={"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64},
        )


def random_strategy(
    strategy: BracketStrategy,
    rows: list[int],
    shapes: list[Bracket],
    n_bars: int,
    rng: random.Random,
) -> RandomEntries:
    """As many entries as ``rows``, at random rows from the first signal on, each with one
    of ``shapes``."""
    if not rows:
        raise ValueError(f"{strategy.name} never signals an entry: nothing to compare")
    # Entries can only fill at a later open, so the last bar can't be drawn.
    pool = range(rows[0], n_bars - 1)
    picked = rng.sample(pool, min(len(rows), len(pool)))
    shuffled = rng.sample(shapes, len(shapes))
    return RandomEntries(
        f"random entries like {strategy.name}",
        strategy.symbol,
        dict(zip(sorted(picked), shuffled, strict=False)),
    )


@dataclass(frozen=True)
class Control:
    strategy: str
    expectancy_r: float | None
    trades: int
    paths: list[float | None]
    """Each random path's net expectancy per trade (R); None if it never traded."""

    @property
    def scored(self) -> list[float]:
        return [p for p in self.paths if p is not None]

    @property
    def beaten_share(self) -> float | None:
        if self.expectancy_r is None or not self.scored:
            return None
        return sum(self.expectancy_r > p for p in self.scored) / len(self.scored)

    def quantile(self, q: float) -> float | None:
        ordered = sorted(self.scored)
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else None

    @property
    def verdict(self) -> str:
        share = self.beaten_share
        if share is None:
            return "NO TRADES"
        return "BEATS RANDOM" if share >= BEAT_SHARE else "NO BETTER THAN RANDOM"


def run_control(
    strategy: BracketStrategy,
    bars: pl.DataFrame,
    interval: Interval,
    config: Config,
    cash: Decimal,
    paths: int = PATHS,
    seed: int = SEED,
    on_path: Callable[[int], None] = lambda _: None,
) -> Control:
    """The strategy once, then ``paths`` random-entry copies on the same bars."""
    own = compute_metrics(run_backtest(strategy, bars, interval, config, cash))
    rows, shapes = brackets_of(strategy.brackets(bars), bars)
    rng = random.Random(seed)
    results: list[float | None] = []
    for i in range(paths):
        copy = random_strategy(strategy, rows, shapes, bars.height, rng)
        results.append(
            compute_metrics(run_backtest(copy, bars, interval, config, cash)).expectancy_r
        )
        on_path(i + 1)
    trades = own.round_trips if own.round_trips is not None else own.trades
    return Control(strategy.name, own.expectancy_r, trades, results)


def format_control(c: Control, market: str) -> str:
    def r(x: float | None) -> str:
        return "n/a" if x is None else f"{x:+.2f}R"

    share = c.beaten_share
    return "\n".join(
        [
            f"Random-entry control  ·  {c.strategy}  ·  {market}",
            "",
            f"  Strategy          {r(c.expectancy_r)} a trade after costs, {c.trades} trades",
            f"  Random entries    median {r(c.quantile(0.5))}, 95th percentile "
            f"{r(c.quantile(0.95))} ({len(c.scored)} of {len(c.paths)} paths traded)",
            f"  Beats             {'n/a' if share is None else f'{share:.0%}'} of random paths "
            f"({BEAT_SHARE:.0%} needed)",
            "",
            f"  Verdict: {c.verdict}",
            "  Same stops, targets, sizing and costs; only the entry times are random.",
        ]
    )
