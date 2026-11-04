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

from typing import Protocol

import polars as pl


class LookaheadError(Exception):
    """A strategy's weight at bar t changed when bars after t were removed."""


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
