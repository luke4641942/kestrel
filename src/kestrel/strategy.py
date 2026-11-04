"""Strategy interface and the no-lookahead guard.

A strategy never places orders. It turns closed bars into a *target weight* per bar:
the fraction of equity it wants in the symbol after that bar closes (long-only spot,
so 0 to 1; the rest is cash). Engines and, later, the allocator turn targets into
orders, so the same strategy runs unchanged in backtest, paper and live.

Lookahead is checked, not trusted. ``check_no_lookahead`` recomputes the weights on
truncated histories and requires them to match the weights computed on the full
history. A strategy that peeks at a later bar (``shift(-1)``, a centred rolling
window, normalising by the full-sample mean) gives different answers when the
future is cut off, and is refused before any backtest runs.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

import polars as pl


class LookaheadError(ValueError):
    """A strategy's weight at bar t changed when bars after t were removed.

    A ValueError: it is invalid input, so the CLI, API and jobs report it with the
    other validation errors instead of crashing."""


class Strategy(Protocol):
    name: str
    symbol: str

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        """One weight in [0, 1] per row of ``bars``, using only that row and earlier."""
        ...


class BuyAndHold:
    """Fully invested from the first bar. Kestrel's benchmark, run through the engine
    like any strategy so it pays the same costs."""

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self.symbol = symbol
        self.name = f"buy-and-hold {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        return pl.Series("weight", [1.0] * bars.height, dtype=pl.Float64)


class MovingAverageTrend:
    """Long when the fast moving average of closes is above the slow one, else cash.

    An ENGINE TEST, not research: a textbook rule with known behaviour, used to check
    the engines (and, later, that the vectorised and event-driven engines agree).
    Its results say nothing about whether trend following works.
    """

    def __init__(self, symbol: str = "BTCUSDT", fast: int = 20, slow: int = 100) -> None:
        if not 1 <= fast < slow:
            raise ValueError("need 1 <= fast < slow")
        self.symbol = symbol
        self.fast, self.slow = fast, slow
        self.name = f"moving-average trend {fast}/{slow} {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        close = bars["close"]
        # Trailing windows only; until the slow average has a full window, stay in cash.
        fast = close.rolling_mean(self.fast)
        slow = close.rolling_mean(self.slow)
        return (fast > slow).fill_null(False).cast(pl.Float64).alias("weight")


@dataclass(frozen=True)
class Param:
    name: str
    default: int
    low: int
    high: int
    description: str


@dataclass(frozen=True)
class StrategySpec:
    key: str
    kind: Literal["benchmark", "engine-test", "research"]
    description: str
    factory: Callable[..., Strategy]
    params: tuple[Param, ...] = ()


STRATEGIES: dict[str, StrategySpec] = {
    spec.key: spec
    for spec in (
        StrategySpec(
            "buy-and-hold",
            "benchmark",
            "Fully invested from the first bar. The benchmark every strategy must beat.",
            BuyAndHold,
        ),
        StrategySpec(
            "ma-trend",
            "engine-test",
            "Long when the fast moving average is above the slow one. An engine test, "
            "not a research result.",
            MovingAverageTrend,
            (
                Param("fast", 20, 2, 200, "Bars in the fast moving average"),
                Param("slow", 100, 3, 1000, "Bars in the slow moving average"),
            ),
        ),
    )
}
"""Strategy implementations available to the CLI and API. Research strategies are
added here once their research note is frozen (CLAUDE.md: no strategy research until
the backtester is validated)."""


def resolve_params(key: str, params: dict[str, int] | None = None) -> dict[str, int]:
    """Fill defaults and check every parameter is known and inside its allowed range."""
    spec = spec_for(key)
    given = dict(params or {})
    unknown = set(given) - {p.name for p in spec.params}
    if unknown:
        raise ValueError(f"{key} has no parameter(s) {', '.join(sorted(unknown))}")
    out: dict[str, int] = {}
    for p in spec.params:
        value = given.get(p.name, p.default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} parameter {p.name} must be a whole number")
        if not p.low <= value <= p.high:
            raise ValueError(f"{key} parameter {p.name} must be {p.low} to {p.high}, got {value}")
        out[p.name] = value
    return out


def spec_for(key: str) -> StrategySpec:
    if key not in STRATEGIES:
        raise ValueError(f"unknown strategy {key!r}; one of {', '.join(STRATEGIES)}")
    return STRATEGIES[key]


def make_strategy(key: str, symbol: str, params: dict[str, int] | None = None) -> Strategy:
    return spec_for(key).factory(symbol.upper(), **resolve_params(key, params))


def strategy_source(key: str) -> str:
    """The implementation's source code, for display. What the backtester runs."""
    return inspect.getsource(spec_for(key).factory)


def validated_weights(strategy: Strategy, bars: pl.DataFrame, checkpoints: int = 8) -> pl.Series:
    """Compute weights, check their shape and range, and check for lookahead."""
    weights = strategy.target_weights(bars)
    if weights.len() != bars.height:
        raise ValueError(f"{strategy.name}: {weights.len()} weights for {bars.height} bars")
    if weights.null_count() or weights.is_nan().any():
        raise ValueError(f"{strategy.name}: weights contain nulls or NaN")
    if weights.len() and (weights.min() < 0 or weights.max() > 1):  # type: ignore[operator]
        raise ValueError(f"{strategy.name}: weights must be in [0, 1] (long-only spot)")
    check_no_lookahead(strategy, bars, weights, checkpoints)
    return weights


def check_no_lookahead(
    strategy: Strategy, bars: pl.DataFrame, weights: pl.Series, checkpoints: int = 8
) -> None:
    """Recompute on prefixes of the history; every shared weight must be identical."""
    n = bars.height
    if n < 2:
        return
    cuts = sorted({max(1, (n * k) // (checkpoints + 1)) for k in range(1, checkpoints + 1)})
    for cut in cuts:
        prefix = strategy.target_weights(bars.head(cut))
        expected = weights.head(cut)
        if prefix.len() != cut or not prefix.equals(expected, check_names=False):
            diff = (prefix.head(cut) != expected).arg_true() if prefix.len() == cut else None
            first = int(diff[0]) if diff is not None and diff.len() else cut - 1
            raise LookaheadError(
                f"{strategy.name}: weight at bar {first} "
                f"({bars['open_time'][first]}) changes when bars after {cut - 1} are removed. "
                "It is using information from the future."
            )
