"""Forex strategies (P6): the interface, the registry, and the lookahead check.

A forex strategy sees every pair's daily candles (kestrel.data.fx, weekend candles
dropped) and the interest rates (kestrel.data.rates; a decision may use only
``Rates.known_rate``), and returns one signed target weight per pair per day: a frame
of ``open_time`` (the union of the pairs' days) and one column per pair. The weight for a
day is decided at that day's close and traded at the next open
(kestrel.backtest.fx_engine). The sum of |weights| may not pass ``[fx] max_gross_leverage``.

Keys start with ``fx:`` so a note's implementation says which market it trades. Notes name
them like any strategy; ``kestrel fx-backtest`` runs them, and the crypto backtest refuses.

**Lookahead** is checked the way crypto's is (kestrel.strategy.check_no_lookahead): the
weights are recomputed on histories cut at several days, and every weight up to the cut
must be unchanged. A strategy that peeks at a later day changes an earlier weight when
that day is removed.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import polars as pl

from kestrel.data.rates import Rates
from kestrel.strategy import Param


class FxStrategy(Protocol):
    name: str

    def target_weights(self, bars: dict[str, pl.DataFrame], rates: Rates) -> pl.DataFrame: ...


@dataclass(frozen=True)
class FxSpec:
    key: str
    description: str
    factory: Callable[..., FxStrategy]
    params: tuple[Param, ...] = ()


FX_STRATEGIES: dict[str, FxSpec] = {}
"""Filled by each forex strategy's note, never before its rule is written down."""


def register(spec: FxSpec) -> FxSpec:
    if not spec.key.startswith("fx:"):
        raise ValueError(f"forex strategy keys start with 'fx:', got {spec.key!r}")
    FX_STRATEGIES[spec.key] = spec
    return spec


def resolve_fx_params(key: str, params: dict[str, int] | None = None) -> dict[str, int]:
    if key not in FX_STRATEGIES:
        known = ", ".join(FX_STRATEGIES) or "none yet"
        raise ValueError(f"unknown forex strategy {key!r}; one of {known}")
    spec = FX_STRATEGIES[key]
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


def make_fx_strategy(key: str, params: dict[str, int] | None = None) -> FxStrategy:
    return FX_STRATEGIES[key].factory(**resolve_fx_params(key, params))


def calendar(bars: dict[str, pl.DataFrame]) -> list[datetime]:
    """Every day any pair has a candle: the rows a strategy's weights must have."""
    days: set[datetime] = set()
    for frame in bars.values():
        days.update(frame["open_time"].to_list())
    return sorted(days)


def _cut(bars: dict[str, pl.DataFrame], last: datetime) -> dict[str, pl.DataFrame]:
    return {p: f.filter(pl.col("open_time") <= last) for p, f in bars.items()}


def validated_fx_weights(
    strategy: FxStrategy,
    bars: dict[str, pl.DataFrame],
    rates: Rates,
    max_gross: float,
    checkpoints: int = 6,
) -> pl.DataFrame:
    """Weights checked for shape, range and lookahead."""
    pairs = sorted(bars)
    days = calendar(bars)
    weights = strategy.target_weights(bars, rates)
    expected = ["open_time", *pairs]
    if sorted(weights.columns) != sorted(expected):
        raise ValueError(f"{strategy.name}: weight columns {weights.columns}, need {expected}")
    weights = weights.select(expected).sort("open_time")
    if weights["open_time"].to_list() != days:
        raise ValueError(f"{strategy.name}: one weight row per day any pair trades")
    values = weights.select(pairs)
    if values.null_count().sum_horizontal().item():
        raise ValueError(f"{strategy.name}: weights contain nulls (0 means no position)")
    if any(not math.isfinite(v) for row in values.iter_rows() for v in row):
        raise ValueError(f"{strategy.name}: weights contain NaN or infinity")
    gross = values.select(pl.sum_horizontal(pl.all().abs())).to_series()
    most = float(gross.max()) if gross.len() else 0.0  # type: ignore[arg-type]
    if most > max_gross + 1e-9:
        raise ValueError(
            f"{strategy.name}: asks for {most:.2f}x gross, over the {max_gross:g}x cap"
        )
    n = len(days)
    for k in range(1, checkpoints + 1):
        i = n * k // (checkpoints + 1)
        if i < 2:
            continue
        early = strategy.target_weights(_cut(bars, days[i]), rates).select(expected)
        early = early.sort("open_time")
        full = weights.head(early.height)
        for pair in pairs:
            a, b = early[pair].to_list(), full[pair].to_list()
            for j, (x, y) in enumerate(zip(a, b, strict=True)):
                if not math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12):
                    raise ValueError(
                        f"{strategy.name}: lookahead: {pair}'s weight on {days[j]:%Y-%m-%d} "
                        f"changes when days after {days[i]:%Y-%m-%d} are removed"
                    )
    return weights


# The registered forex strategies, each added with its note (imported last: they register
# themselves through ``register`` above).
from kestrel.strategies import fx_carry as _fx_carry  # noqa: E402, F401
from kestrel.strategies import fx_trend as _fx_trend  # noqa: E402, F401
