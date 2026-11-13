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
from typing import Literal, Protocol, cast, runtime_checkable

import polars as pl

from kestrel.strategies.anchor import HighAnchor
from kestrel.strategies.basket import LargeCoinBasket
from kestrel.strategies.funding import FundingHold, FundingTrend
from kestrel.strategies.levels import LevelBreakout, LevelRetest
from kestrel.strategies.momentum import CrossSectionalMomentum
from kestrel.strategies.pivots import PivotReversal
from kestrel.strategies.reversal import DropReversal
from kestrel.strategies.rotation import DualMomentum
from kestrel.strategies.sentiment import FearGreedContrarian
from kestrel.strategies.structure import StructurePullback
from kestrel.strategies.trend import TrendVolTarget
from kestrel.strategies.trend_basket import TrendBasket
from kestrel.strategies.trendlines import (
    SwingPullback,
    TrendlineBounce,
    TrendlineBreakout,
    WedgeBreakout,
)
from kestrel.strategies.volhold import VolManagedHold
from kestrel.strategies.weekday import MondayMidPullback
from kestrel.strategies.weekly import WeeklyMomentum


class LookaheadError(ValueError):
    """A strategy's weight at bar t changed when bars after t were removed.

    A ValueError: it is invalid input, so the CLI, API and jobs report it with the
    other validation errors instead of crashing."""


UNIVERSE = "UNIVERSE"
"""The market a cross-sectional strategy runs on: every coin in kestrel.data.universe."""


class PortfolioStrategy(Protocol):
    """Holds several coins at once. Returns decisions (open_time, symbol, weight); see
    kestrel.backtest.portfolio for what they mean."""

    name: str

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame: ...


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
    open), ``stop`` and ``target`` (price levels, set on entry rows), and optionally
    ``exit`` (sell an open trade at the next open, e.g. a time exit). ``target`` may be
    null: the trade then ends at its stop, an exit signal or the end of the data. The
    strategy decides *where* the levels are; the engine decides *how much* to buy, from
    the risk per trade in kestrel.toml and the distance to the stop, so a stop is
    always required.
    """

    name: str
    symbol: str

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame: ...


BRACKET_SCHEMA = {"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64, "exit": pl.Boolean}
OPTIONAL_BRACKET_COLUMNS = {"exit"}


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
    factory: Callable[..., Strategy | BracketStrategy | PortfolioStrategy]
    params: tuple[Param, ...] = ()
    universe: bool = False
    """Ranks the whole coin universe (a PortfolioStrategy) instead of trading one market."""
    features: tuple[str, ...] = ()
    """Information beyond prices it needs on its bars (kestrel.data.features)."""


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
            "trend-vol-target",
            "research",
            "Long when the 20, 60 and 120-day trends agree (scaled by how many do), sized "
            "down when volatility is above a target. Research: runs only through its frozen "
            "note.",
            TrendVolTarget,
            (
                Param("fast_days", 20, 5, 60, "Shortest trend lookback, in days"),
                Param("mid_days", 60, 20, 180, "Middle trend lookback, in days"),
                Param("slow_days", 120, 60, 365, "Longest trend lookback, in days"),
                Param("vol_days", 30, 10, 90, "Days of returns that measure volatility"),
                Param("target_vol_pct", 50, 10, 150, "Annual volatility to size towards, in %"),
            ),
        ),
        StrategySpec(
            "monday-pullback",
            "research",
            "In a week that breaks Monday's high (AEST) before its low, buy the first pullback "
            "to Monday's midpoint; stop at Monday's low, out at the end of Friday. 1h bars. "
            "Research: runs only through its frozen note.",
            MondayMidPullback,
            (
                Param("tolerance_bps", 50, 10, 200, "How close to the midpoint counts, in bps"),
                Param("exit_day", 5, 2, 7, "Weekday whose last bar closes the trade (5 = Friday)"),
            ),
        ),
        StrategySpec(
            "pivot-reversal",
            "research",
            "Buy when a rebound of 3.5% off the running low confirms a pivot low (a zigzag "
            "pivot, known only then); stop 1.5% below, target 3% above. Research: runs only "
            "through its frozen note.",
            PivotReversal,
            (
                Param("swing_bps", 350, 100, 1000, "Move that confirms a pivot, in bps"),
                Param("stop_bps", 150, 25, 1000, "Stop below the signal close, in bps"),
                Param("target_bps", 300, 25, 3000, "Target above the signal close, in bps"),
            ),
        ),
        StrategySpec(
            "xs-momentum",
            "research",
            "Every Sunday, rank the liquid coins (delisted ones included) on 7, 14 and 28-day "
            "returns and hold the top 5 equally, each only while its own 28-day trend is up. "
            "Daily bars, whole universe. Research: runs only through its frozen note.",
            CrossSectionalMomentum,
            (
                Param("top_n", 5, 1, 20, "Coins held at most"),
                Param("fast_days", 7, 2, 30, "Shortest return lookback, in days"),
                Param("mid_days", 14, 5, 60, "Middle return lookback, in days"),
                Param("slow_days", 28, 10, 120, "Longest lookback; also the absolute filter"),
                Param("min_history_days", 180, 30, 730, "Unbroken history needed to be ranked"),
                Param("min_volume_musd", 20, 1, 500, "Median daily volume needed, in million USDT"),
            ),
            universe=True,
        ),
        StrategySpec(
            "fear-greed",
            "research",
            "Buy when the Crypto Fear & Greed Index reads Extreme Fear (25 or below), sell "
            "when it reads Extreme Greed (76 or above), hold in between. Uses the latest "
            "value known by each close. Research: runs only through its frozen note.",
            FearGreedContrarian,
            (
                Param("fear_max", 25, 5, 49, "Buy at or below this reading"),
                Param("greed_min", 76, 51, 95, "Sell at or above this reading"),
            ),
            features=("fear_greed",),
        ),
        StrategySpec(
            "drop-reversal",
            "research",
            "Buy after a daily drop of more than 2 standard deviations of the previous 30 "
            "days' returns, hold 3 days; another big drop restarts the clock. Research: runs "
            "only through its frozen note.",
            DropReversal,
            (
                Param("drop_sigma_pct", 200, 100, 500, "Drop size, in % of a standard deviation"),
                Param("vol_days", 30, 10, 120, "Days of returns that set the standard deviation"),
                Param("hold_days", 3, 1, 20, "Days held after a big drop"),
            ),
        ),
        StrategySpec(
            "funding-hold",
            "research",
            "Hold the coin, but go to cash while its perpetual futures funding over the last "
            "7 days is 30% a year or more (crowded leveraged longs). Research: runs only "
            "through its frozen note.",
            FundingHold,
            (Param("crowded_pct", 30, 5, 200, "Annualised 7-day funding that counts as crowded"),),
            features=("funding_7d",),
        ),
        StrategySpec(
            "funding-trend",
            "research",
            "Trend-vol-target with its default settings, but in cash while the coin's 7-day "
            "perpetual funding is 30% a year or more. Research: runs only through its frozen "
            "note.",
            FundingTrend,
            (Param("crowded_pct", 30, 5, 200, "Annualised 7-day funding that counts as crowded"),),
            features=("funding_7d",),
        ),
        StrategySpec(
            "trend-basket",
            "research",
            "Trend-vol-target's rule on each of the 10 most-traded coins (weekly, delisted "
            "coins included), each in a 1/10 slot sized by its own trend and volatility. "
            "Research: runs only through its frozen note.",
            TrendBasket,
            (
                Param("top_n", 10, 2, 30, "Coins in the basket, each with an equal slot"),
                Param("min_history_days", 180, 120, 730, "Unbroken history a coin needs"),
                Param("min_volume_musd", 20, 1, 500, "Median daily volume needed, in million USDT"),
                Param("target_vol_pct", 50, 10, 150, "Annual volatility each coin is sized to, %"),
            ),
            universe=True,
        ),
        StrategySpec(
            "vol-hold",
            "research",
            "Hold the coin sized by target volatility over its last 30 days' volatility, "
            "capped at fully in; no trend rule. Trend-vol-target without the trend. "
            "Research: runs only through its frozen note.",
            VolManagedHold,
            (
                Param("vol_days", 30, 10, 90, "Days of returns that measure volatility"),
                Param("target_vol_pct", 50, 10, 150, "Annual volatility to size towards, in %"),
            ),
        ),
        StrategySpec(
            "high-anchor",
            "research",
            "Every Sunday, hold the 3 of the 20 most-traded coins (a year of history each) "
            "trading nearest their 52-week high, equally. Research: runs only through its "
            "frozen note.",
            HighAnchor,
            (
                Param("hold_n", 3, 1, 10, "Coins held, equally"),
                Param("basket_n", 20, 2, 50, "Most-traded coins it chooses from"),
                Param("min_volume_musd", 20, 1, 500, "Median daily volume needed, in million USDT"),
            ),
            universe=True,
        ),
        StrategySpec(
            "large-basket",
            "control",
            "The basket strategies' comparison: the most-traded coins (weekly, delisted coins "
            "included) held in equal weights, with no signal.",
            LargeCoinBasket,
            (
                Param("top_n", 10, 1, 50, "Coins held, equally"),
                Param("min_history_days", 180, 30, 730, "Unbroken history a coin needs"),
                Param("min_volume_musd", 20, 1, 500, "Median daily volume needed, in million USDT"),
            ),
            universe=True,
        ),
        StrategySpec(
            "weekly-momentum",
            "research",
            "Hold the coin next week if its Sunday-to-Sunday return was positive, else cash. "
            "Research: runs only through its frozen note.",
            WeeklyMomentum,
        ),
        StrategySpec(
            "btc-eth-rotation",
            "research",
            "Every Sunday, hold whichever of BTC and ETH rose more over 90 days, if it rose "
            "at all; otherwise cash. Research: runs only through its frozen note.",
            DualMomentum,
            (Param("lookback_days", 90, 20, 365, "Days of return compared"),),
            universe=True,
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
    spec = spec_for(key)
    if spec.universe:
        raise ValueError(f"{key} ranks the whole coin universe; run it on {UNIVERSE}, not {symbol}")
    return cast(
        Strategy | BracketStrategy, spec.factory(symbol.upper(), **resolve_params(key, params))
    )


def make_portfolio_strategy(key: str, params: dict[str, int] | None = None) -> PortfolioStrategy:
    spec = spec_for(key)
    if not spec.universe:
        raise ValueError(f"{key} trades one market; choose a market instead of {UNIVERSE}")
    return cast(PortfolioStrategy, spec.factory(UNIVERSE, **resolve_params(key, params)))


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
    frame = _normalised_brackets(strategy.name, frame)
    entries = frame.filter("enter")
    bad = entries.filter(
        pl.col("stop").is_null()
        | pl.col("stop").is_nan()
        | pl.col("target").is_nan()
        | (pl.col("stop") <= 0)
        | (pl.col("stop") >= pl.col("target"))  # null when there is no target: passes
    )
    if bad.height:
        raise ValueError(f"{strategy.name}: every entry needs 0 < stop, and stop < target if set")

    def compute(b: pl.DataFrame) -> pl.DataFrame:
        return _normalised_brackets(strategy.name, strategy.brackets(b))

    _check_prefixes(strategy.name, compute, bars, frame, checkpoints)
    return frame


def _normalised_brackets(name: str, frame: pl.DataFrame) -> pl.DataFrame:
    """Every bracket column, in schema order and type, with an absent ``exit`` as False."""
    columns = set(frame.columns)
    required = set(BRACKET_SCHEMA) - OPTIONAL_BRACKET_COLUMNS
    if not required <= columns <= set(BRACKET_SCHEMA):
        raise ValueError(
            f"{name}: bracket columns must be {sorted(required)}, optionally "
            f"{sorted(OPTIONAL_BRACKET_COLUMNS)}"
        )
    if "exit" not in columns:
        frame = frame.with_columns(exit=pl.lit(False))
    frame = frame.select([pl.col(c).cast(t) for c, t in BRACKET_SCHEMA.items()])
    return frame.with_columns(pl.col("enter").fill_null(False), pl.col("exit").fill_null(False))


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
