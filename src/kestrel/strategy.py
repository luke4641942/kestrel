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
from typing import Literal, Protocol, runtime_checkable

import polars as pl

from kestrel.strategies.levels import LevelBreakout, LevelRetest
from kestrel.strategies.structure import StructurePullback
from kestrel.strategies.trendlines import (
    SwingPullback,
    TrendlineBounce,
    TrendlineBreakout,
    WedgeBreakout,
)


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


@runtime_checkable
class BracketStrategy(Protocol):
    """A strategy that enters with its own stop and target instead of holding weights.

    ``brackets`` returns one row per bar with columns ``enter`` (go long at the next
    open), ``stop`` and ``target`` (price levels, set on entry rows). The strategy
    decides *where* the stop and target are; the engine decides *how much* to buy,
    from the risk per trade in kestrel.toml and the distance to the stop.
    """

    name: str
    symbol: str

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame: ...


BRACKET_SCHEMA = {"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64}


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


class ChannelBreakout:
    """Enter when the close breaks above the prior N-bar high; stop below the recent low;
    target a fixed multiple of the risk.

    An ENGINE TEST, not research: it exists to exercise stops, targets and risk-based
    sizing. Its results say nothing about whether breakouts work.
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        lookback: int = 20,
        stop_lookback: int = 10,
        target_r: int = 3,
    ) -> None:
        self.symbol = symbol
        self.lookback, self.stop_lookback, self.target_r = lookback, stop_lookback, target_r
        self.name = f"channel breakout {lookback}/{stop_lookback} {target_r}R {symbol}"

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        # Prior bars only for the channel; the current bar's close is known at its close.
        channel = pl.col("high").shift(1).rolling_max(self.lookback)
        stop = pl.col("low").rolling_min(self.stop_lookback)
        enter = ((pl.col("close") > channel) & (stop < pl.col("close"))).fill_null(False)
        target = pl.col("close") + self.target_r * (pl.col("close") - stop)
        return bars.select(
            enter.alias("enter"),
            pl.when(enter).then(stop).alias("stop"),
            pl.when(enter).then(target).alias("target"),
        )


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
    kind: Literal["benchmark", "engine-test", "research", "control"]
    description: str
    factory: Callable[..., Strategy | BracketStrategy]
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
            "channel-breakout",
            "engine-test",
            "Breakout entry with a stop below the recent low and a fixed-R target. An "
            "engine test for stops, targets and risk sizing, not a research result.",
            ChannelBreakout,
            (
                Param("lookback", 20, 2, 500, "Bars in the breakout channel"),
                Param("stop_lookback", 10, 1, 200, "Bars whose lowest low sets the stop"),
                Param("target_r", 3, 1, 20, "Target as a multiple of the risk to the stop"),
            ),
        ),
        StrategySpec(
            "structure-pullback",
            "research",
            "Change of character, then a pullback into the 61.8% retracement and a fair "
            "value gap; stop at the leg low, fixed-R target. Research: runs only through "
            "its frozen note.",
            StructurePullback,
            (
                Param("swing_bars", 3, 1, 6, "Bars each side that make a swing point"),
                Param("target_r", 4, 2, 8, "Target as a multiple of the risk to the stop"),
                Param("expiry_bars", 30, 5, 120, "Bars a setup waits for its pullback"),
            ),
        ),
        StrategySpec(
            "level-retest",
            "research",
            "Break of a horizontal level made of two or more swing highs, then a "
            "retest that holds; stop below the level, fixed-R target. Research: runs only "
            "through its frozen note.",
            LevelRetest,
            (
                Param("swing_bars", 3, 1, 6, "Bars each side that make a swing point"),
                Param(
                    "level_tol_atr_pct",
                    50,
                    10,
                    200,
                    "Swing highs within this % of an ATR form one level",
                ),
                Param(
                    "breakout_atr_pct",
                    50,
                    0,
                    200,
                    "Close this % of an ATR beyond the level or line",
                ),
                Param(
                    "touch_atr_pct",
                    25,
                    0,
                    100,
                    "A retest's low comes within this % of an ATR of the level",
                ),
                Param("retest_bars", 30, 3, 120, "Bars a breakout waits for its retest"),
                Param("lookback_bars", 120, 20, 500, "Bars back a level's swing highs may be"),
                Param("target_r", 3, 1, 8, "Target as a multiple of the risk to the stop"),
            ),
        ),
        StrategySpec(
            "level-breakout",
            "control",
            "The level retest's comparison: buy the breakout close itself, without "
            "waiting for a retest. The retest has to beat this.",
            LevelBreakout,
            (
                Param("swing_bars", 3, 1, 6, "Bars each side that make a swing point"),
                Param(
                    "level_tol_atr_pct",
                    50,
                    10,
                    200,
                    "Swing highs within this % of an ATR form one level",
                ),
                Param(
                    "breakout_atr_pct",
                    50,
                    0,
                    200,
                    "Close this % of an ATR beyond the level or line",
                ),
                Param("lookback_bars", 120, 20, 500, "Bars back a level's swing highs may be"),
                Param("target_r", 3, 1, 8, "Target as a multiple of the risk to the stop"),
            ),
        ),
        StrategySpec(
            "trendline-bounce",
            "research",
            "In an uptrend (rising swing highs and lows), buy a touch of the rising line "
            "through the last two swing lows that closes above it; stop below the line, "
            "fixed-R target. Research: runs only through its frozen note.",
            TrendlineBounce,
            (
                Param("swing_bars", 3, 1, 6, "Bars each side that make a swing point"),
                Param(
                    "touch_atr_pct",
                    25,
                    0,
                    100,
                    "A touch's low comes within this % of an ATR of the line",
                ),
                Param("stop_atr_pct", 100, 10, 300, "Stop this % of an ATR below the line"),
                Param("target_r", 3, 1, 8, "Target as a multiple of the risk to the stop"),
            ),
        ),
        StrategySpec(
            "swing-pullback",
            "control",
            "The trendline bounce's comparison: the same uptrend, buying a pullback near "
            "the last swing low with no line drawn. The bounce has to beat this.",
            SwingPullback,
            (
                Param("swing_bars", 3, 1, 6, "Bars each side that make a swing point"),
                Param(
                    "pullback_atr_pct",
                    150,
                    10,
                    400,
                    "A pullback's low comes within this % of an ATR of the swing low",
                ),
                Param("stop_atr_pct", 100, 10, 300, "Stop this % of an ATR below the swing low"),
                Param("target_r", 3, 1, 8, "Target as a multiple of the risk to the stop"),
            ),
        ),
        StrategySpec(
            "trendline-breakout",
            "research",
            "Buy a close above the falling line through the last two swing highs; stop at "
            "the lowest low since the second swing high, fixed-R target. Compared with the "
            "channel breakout. Research: runs only through its frozen note.",
            TrendlineBreakout,
            (
                Param("swing_bars", 3, 1, 6, "Bars each side that make a swing point"),
                Param(
                    "breakout_atr_pct",
                    50,
                    0,
                    200,
                    "Close this % of an ATR beyond the level or line",
                ),
                Param("target_r", 3, 1, 8, "Target as a multiple of the risk to the stop"),
            ),
        ),
        StrategySpec(
            "wedge-breakout",
            "research",
            "Buy a close above the upper line of a falling wedge (two falling, converging "
            "lines); stop at the last swing low, fixed-R target. Compared with the trendline "
            "breakout. Research: runs only through its frozen note.",
            WedgeBreakout,
            (
                Param("swing_bars", 3, 1, 6, "Bars each side that make a swing point"),
                Param(
                    "converge_pct",
                    60,
                    10,
                    95,
                    "The lines' gap narrows to at most this % of where it started",
                ),
                Param(
                    "breakout_atr_pct",
                    50,
                    0,
                    200,
                    "Close this % of an ATR beyond the level or line",
                ),
                Param("target_r", 3, 1, 8, "Target as a multiple of the risk to the stop"),
            ),
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
"""Strategy implementations available to the CLI and API. Research strategies run only
as trials of a frozen research note (``kestrel.research.run_target``); benchmarks,
engine tests and controls run directly."""


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


def make_strategy(
    key: str, symbol: str, params: dict[str, int] | None = None
) -> Strategy | BracketStrategy:
    return spec_for(key).factory(symbol.upper(), **resolve_params(key, params))


def strategy_source(key: str) -> str:
    """The implementation's source code, for display. What the backtester runs.

    Research strategies (and the controls they are compared with) live in their own
    modules under kestrel.strategies, whose docstring states the rules, so the whole
    module is shown."""
    spec = spec_for(key)
    module = inspect.getmodule(spec.factory)
    if spec.kind in ("research", "control") and module is not None:
        return inspect.getsource(module)
    return inspect.getsource(spec.factory)


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


def validated_brackets(
    strategy: BracketStrategy, bars: pl.DataFrame, checkpoints: int = 8
) -> pl.DataFrame:
    """Compute brackets, check their shape and levels, and check for lookahead."""
    frame = strategy.brackets(bars)
    if frame.height != bars.height:
        raise ValueError(f"{strategy.name}: {frame.height} bracket rows for {bars.height} bars")
    if set(frame.columns) != set(BRACKET_SCHEMA):
        raise ValueError(f"{strategy.name}: bracket columns must be {sorted(BRACKET_SCHEMA)}")
    frame = frame.select([pl.col(c).cast(t) for c, t in BRACKET_SCHEMA.items()])
    frame = frame.with_columns(pl.col("enter").fill_null(False))
    entries = frame.filter("enter")
    bad = entries.filter(
        pl.col("stop").is_null()
        | pl.col("target").is_null()
        | pl.col("stop").is_nan()
        | pl.col("target").is_nan()
        | (pl.col("stop") <= 0)
        | (pl.col("stop") >= pl.col("target"))
    )
    if bad.height:
        raise ValueError(f"{strategy.name}: every entry needs 0 < stop < target")
    _check_prefixes(strategy.name, strategy.brackets, bars, frame, checkpoints)
    return frame


def check_no_lookahead(
    strategy: Strategy, bars: pl.DataFrame, weights: pl.Series, checkpoints: int = 8
) -> None:
    """Recompute on prefixes of the history; every shared weight must be identical."""
    _check_prefixes(strategy.name, strategy.target_weights, bars, weights.to_frame(), checkpoints)


def _check_prefixes(
    name: str,
    compute: Callable[[pl.DataFrame], pl.Series | pl.DataFrame],
    bars: pl.DataFrame,
    full: pl.DataFrame,
    checkpoints: int,
) -> None:
    """Rows computed on a truncated history must equal the same rows computed on all of it.

    Works for both strategy styles: a weight Series or a bracket DataFrame."""
    n = bars.height
    if n < 2:
        return
    cuts = sorted({max(1, (n * k) // (checkpoints + 1)) for k in range(1, checkpoints + 1)})
    for cut in cuts:
        out = compute(bars.head(cut))
        prefix = out.to_frame() if isinstance(out, pl.Series) else out
        expected = full.head(cut)
        first = cut - 1
        if prefix.shape == expected.shape:
            prefix = prefix.rename(dict(zip(prefix.columns, expected.columns, strict=True)))
            if prefix.equals(expected):
                continue
            first = next(i for i in range(cut) if prefix.row(i) != expected.row(i))
        raise LookaheadError(
            f"{name}: decision at bar {first} "
            f"({bars['open_time'][first]}) changes when bars after {cut - 1} are removed. "
            "It is using information from the future."
        )
