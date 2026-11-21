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

from kestrel.strategies.altcoin_rotation import AltcoinRegimeRotation
from kestrel.strategies.anchor import HighAnchor
from kestrel.strategies.basket import LargeCoinBasket
from kestrel.strategies.basket_breakout import BasketBreakout
from kestrel.strategies.bollinger_pullback import BollingerPullback
from kestrel.strategies.brake import TrendVolTargetBrake, WeeklyMomentumBrake
from kestrel.strategies.btc_regime import BtcRegimeTrend
from kestrel.strategies.btc_rotation import (
    CONFIRM_4,
    CONFIRM_8,
    LARGE_4,
    LARGE_8,
    BtcRegimeRotation,
    BtcRegimeRotation4,
    BtcRegimeRotationConfirm4,
    BtcRegimeRotationConfirm8,
)
from kestrel.strategies.choch_fvg import ChochFvg
from kestrel.strategies.crash_rebound import CrashRebound
from kestrel.strategies.eth_btc import BtcEthHalf, EthBtcValue
from kestrel.strategies.funding import FundingHold, FundingTrend
from kestrel.strategies.levels import LevelBreakout, LevelRetest
from kestrel.strategies.momentum import CrossSectionalMomentum
from kestrel.strategies.monthly_rotation import (
    BtcGoldRotation,
    MonthlyRotation,
    RotationWithStablecoins,
)
from kestrel.strategies.order_flow import OrderFlow
from kestrel.strategies.pattern_macd import PatternMacd
from kestrel.strategies.pivots import PivotReversal
from kestrel.strategies.regime import TrendVolTargetRegime
from kestrel.strategies.reversal import DropReversal
from kestrel.strategies.rotation import DualMomentum
from kestrel.strategies.rsi_dip import RsiDip
from kestrel.strategies.sentiment import FearGreedContrarian
from kestrel.strategies.set_and_forget import SetAndForget
from kestrel.strategies.squeeze import SqueezeBreakout
from kestrel.strategies.stablecoin_hold import StablecoinHold, StablecoinVolHold
from kestrel.strategies.structure import StructurePullback
from kestrel.strategies.tori import ToriTrendline
from kestrel.strategies.trend import TrendVolTarget, TrendVolTargetStablecoin
from kestrel.strategies.trend_basket import TrendBasket
from kestrel.strategies.trend_trio import TrendTrio, TrioHold
from kestrel.strategies.trendlines import (
    SwingPullback,
    TrendlineBounce,
    TrendlineBreakout,
    WedgeBreakout,
)
from kestrel.strategies.turn_of_month import TurnOfMonth
from kestrel.strategies.volatile_macd import VolatileMacd
from kestrel.strategies.volhold import VolManagedHold
from kestrel.strategies.weekday import MondayMidPullback
from kestrel.strategies.weekly import BtcLedWeekly, WeeklyMomentum


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
    null: the trade then ends at its stop, an exit signal or the end of the data.

    Optionally ``limit`` and ``valid_bars`` on an entry row make it a resting buy limit
    instead of a market order: it waits for ``valid_bars`` bars after the signal, fills
    on the first bar whose low reaches it (at the open if the bar opens below it), and is
    replaced by a newer entry. On the bar it fills only the stop can end the trade: when
    the high came is unknown, so a target touched that bar doesn't count. The
    strategy decides *where* the levels are; the engine decides *how much* to buy, from
    the risk per trade in kestrel.toml and the distance to the stop, so a stop is
    always required.
    """

    name: str
    symbol: str

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame: ...


BRACKET_SCHEMA = {
    "enter": pl.Boolean,
    "stop": pl.Float64,
    "target": pl.Float64,
    "exit": pl.Boolean,
    "limit": pl.Float64,
    "valid_bars": pl.Int64,
}
OPTIONAL_BRACKET_COLUMNS = {"exit", "limit", "valid_bars"}


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
    assets: tuple[str, ...] = ()
    """For a universe strategy over named assets only (e.g. BTC and gold): its panel is just
    these symbols, each ingested on its own, instead of the coin universe."""
    coin_events: bool = False
    """A universe strategy that reads coin events (delisting announced, days since its first
    perp; kestrel.data.coin_events) on its panel."""


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
            "tori-trendline",
            "research",
            "Tori Trades' method, long only: buy a close above a falling line through swing highs "
            "touched min_touches times; stop at the low the break started from; no target, a "
            "close below the new rising line through the swing lows since is the exit. A chart "
            "pattern (a rejected family), tested at Luke's request: runs only through its "
            "frozen note.",
            ToriTrendline,
            (
                Param("swing_bars", 3, 1, 6, "Bars each side that make a swing point"),
                Param("min_touches", 3, 2, 6, "Swing highs touching the line"),
                Param("touch_atr_pct", 50, 10, 150, "How close a touch is, % of an ATR"),
                Param("breakout_atr_pct", 0, 0, 200, "Close this % of an ATR above the line"),
                Param("max_line_bars", 180, 30, 600, "Longest line, bars between its two points"),
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
            "volatile-macd",
            "research",
            "In the regime model's volatile state, hold the coin for a week after the MACD "
            "12/26/9 histogram turns or stays positive; cash otherwise. Research: runs only "
            "through its frozen note.",
            VolatileMacd,
            (Param("hold_days", 7, 1, 30, "Days held after a signal (the study's horizon)"),),
            features=("regime_volatile",),
        ),
        StrategySpec(
            "trend-vol-target-regime",
            "research",
            "Trend-vol-target, in cash on days the regime model says the market is in its most "
            "volatile state (backlog S2). regime_filter 0 is the plain rule, for comparison. "
            "Research: runs only through its frozen note.",
            TrendVolTargetRegime,
            (
                Param("fast_days", 20, 5, 60, "Shortest trend lookback, in days"),
                Param("mid_days", 60, 20, 180, "Middle trend lookback, in days"),
                Param("slow_days", 120, 60, 365, "Longest trend lookback, in days"),
                Param("vol_days", 30, 10, 90, "Days of returns that measure volatility"),
                Param("target_vol_pct", 50, 10, 150, "Annual volatility to size towards, in %"),
                Param("regime_filter", 1, 0, 1, "1: out in the volatile regime; 0: plain rule"),
            ),
            features=("regime_volatile",),
        ),
        StrategySpec(
            "trend-vol-target-brake",
            "research",
            "Trend-vol-target with a crash brake: sell when a close falls brake_atr x ATR(14) "
            "below the high since entry, then wait for the trend votes to change. Research: "
            "runs only through its frozen note.",
            TrendVolTargetBrake,
            (
                Param("fast_days", 20, 5, 60, "Shortest trend lookback, in days"),
                Param("mid_days", 60, 20, 180, "Middle trend lookback, in days"),
                Param("slow_days", 120, 60, 365, "Longest trend lookback, in days"),
                Param("vol_days", 30, 10, 90, "Days of returns that measure volatility"),
                Param("target_vol_pct", 50, 10, 150, "Annual volatility to size towards, in %"),
                Param("brake_atr", 2, 1, 6, "Brake: ATRs below the high that trip it"),
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
            "pattern-macd",
            "research",
            "A pin, inside, outside or engulfing bar at a swing low, then a close above it "
            "within expiry_bars as MACD's histogram rises; stop at the pattern's low, "
            "target_r target. Research: runs only through its frozen note.",
            PatternMacd,
            (
                Param("swing_bars", 10, 3, 50, "Bars the pattern's low must be lowest of"),
                Param("expiry_bars", 3, 1, 10, "Bars a pattern waits for its breakout"),
                Param("target_r", 2, 1, 6, "Target as a multiple of the risk to the stop"),
            ),
        ),
        StrategySpec(
            "btc-regime-trend",
            "research",
            "Hold an altcoin while BTC closes above its btc_days average and the coin is up "
            "over coin_days, sized to target_vol_pct over its vol_days volatility (capped at "
            "100%). Exploration graduate: runs only through its frozen confirmation note.",
            BtcRegimeTrend,
            (
                Param("btc_days", 50, 20, 200, "BTC's moving average, days"),
                Param("coin_days", 30, 5, 120, "Lookback for the coin's own trend, days"),
                Param("vol_days", 30, 10, 90, "Days of returns in the volatility"),
                Param("target_vol_pct", 50, 10, 100, "Annualised volatility target, %"),
            ),
            features=("btc_close",),
        ),
        StrategySpec(
            "crash-rebound",
            "research",
            "Buy a coin after it falls more than drop_pct% over days, hold hold_days. "
            "Exploration graduate: runs only through its frozen confirmation note.",
            CrashRebound,
            (
                Param("drop_pct", 30, 10, 60, "Fall that counts as a crash, %"),
                Param("days", 7, 1, 30, "Days the fall is measured over"),
                Param("hold_days", 3, 1, 30, "Days held after a crash"),
            ),
        ),
        StrategySpec(
            "set-and-forget",
            "research",
            "While weekly and daily swings make higher highs and lows, buy a 1H bullish "
            "engulfing inside a 4H demand zone (touches swing lows within zone_atr_tenths/10 "
            "x daily ATR); stop below the zone, target_r target. Research: runs only through "
            "its frozen note.",
            SetAndForget,
            (
                Param("swing_bars", 3, 2, 5, "Bars either side of a swing"),
                Param("touches", 3, 2, 5, "4H swing lows a zone must hold"),
                Param("zone_atr_tenths", 5, 2, 10, "Zone width, tenths of daily ATR(14)"),
                Param("lookback_days", 90, 30, 180, "Days of 4H swing lows zones use"),
                Param("target_r", 3, 2, 4, "Target as a multiple of the risk to the stop"),
            ),
        ),
        StrategySpec(
            "choch-fvg",
            "research",
            "A bullish change of character at the New York open (13:00-16:00 UTC), then a "
            "resting limit at the leg's fair value gap midpoint; stop at the gap's first bar's "
            "low, target_r target. Research: runs only through its frozen note.",
            ChochFvg,
            (
                Param("swing_bars", 3, 2, 6, "Bars either side of a swing high"),
                Param("min_gap_atr_tenths", 3, 1, 6, "Smallest gap, tenths of ATR(14)"),
                Param("expiry_bars", 6, 3, 12, "Bars the limit order rests"),
                Param("target_r", 4, 2, 6, "Target as a multiple of the risk to the stop"),
            ),
        ),
        StrategySpec(
            "order-flow",
            "research",
            "Order flow Model 2: a failed breakdown below the previous UTC day's value area, "
            "reclaimed by a bar where buyers took most of the volume; stop at the breakdown's "
            "low, target the point of control, out by the next day. Research: runs only "
            "through its frozen note.",
            OrderFlow,
            (
                Param("bin_bps", 5, 2, 20, "Volume-profile bin, bps of the last close"),
                Param("value_area_pct", 70, 60, 80, "Share of the day's volume in the value area"),
                Param("reclaim_bars", 15, 5, 30, "Bars a breakdown has to be reclaimed"),
            ),
        ),
        StrategySpec(
            "rsi-dip",
            "research",
            "Buy when RSI(rsi_bars) closes below rsi_below above the trend_days average (0: no "
            "filter); sell at a close above yesterday's high, or RSI above exit_rsi_above if set, "
            "or after max_days if set. Research: runs only through its frozen note.",
            RsiDip,
            (
                Param("rsi_bars", 2, 2, 14, "Closes in the RSI"),
                Param("rsi_below", 10, 5, 40, "Buy when RSI closes below this"),
                Param("trend_days", 200, 0, 365, "Trend filter average, days (0: none)"),
                Param("exit_rsi_above", 0, 0, 90, "Sell when RSI closes above (0: QS exit)"),
                Param("max_days", 0, 0, 30, "Time stop, days (0: none)"),
            ),
        ),
        StrategySpec(
            "squeeze-breakout",
            "research",
            "After a Bollinger squeeze (band width at its squeeze_bars-day low within "
            "recent_bars days), buy a close above the upper band while above the trend_days "
            "average; sell a close below the 20-day average. Research: runs only through its "
            "frozen note.",
            SqueezeBreakout,
            (
                Param("squeeze_bars", 120, 40, 365, "Days the band width must be lowest of"),
                Param("recent_bars", 20, 5, 60, "Days after a squeeze a break still counts"),
                Param("trend_days", 200, 50, 365, "Days in the trend average"),
            ),
        ),
        StrategySpec(
            "turn-of-month",
            "research",
            "Hold the coin only over the change of month: the last last_days days and the "
            "first first_days days, cash otherwise. Research: runs only through its frozen note.",
            TurnOfMonth,
            (
                Param("last_days", 0, 0, 10, "Days held at the end of the month"),
                Param("first_days", 1, 0, 10, "Days held at the start of the next month"),
            ),
        ),
        StrategySpec(
            "weekly-momentum",
            "research",
            "Hold the coin next week if its Sunday-to-Sunday return was positive, else cash. "
            "Research: runs only through its frozen note.",
            WeeklyMomentum,
        ),
        StrategySpec(
            "weekly-momentum-brake",
            "research",
            "Weekly momentum with a crash brake: sell when a close falls brake_atr x ATR(14) "
            "below the high since entry, then wait for the next Sunday. Research: runs only "
            "through its frozen note.",
            WeeklyMomentumBrake,
            (Param("brake_atr", 2, 1, 6, "Brake: ATRs below the high that trip it"),),
        ),
        StrategySpec(
            "btc-led-weekly",
            "research",
            "Hold the coin (ETH, SOL) next week if BTC's Sunday-to-Sunday return was positive, "
            "else cash: BTC leads, the alts follow. Research: runs only through its frozen note.",
            BtcLedWeekly,
            features=("btc_close",),
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
            "bollinger-pullback",
            "research",
            "Across liquid coins above their trend_days average: after a close below the 20-day "
            "band at band_sd_tenths/10 SD, a limit buy depth_pct% under it for the next day; "
            "sell after a close with RSI(2) above 50 or max_days closes. Highest 150-day return "
            "first, top_n slots. Research: runs only through its frozen note.",
            BollingerPullback,
            (
                Param("band_sd_tenths", 25, 15, 40, "Lower band, tenths of a standard deviation"),
                Param("depth_pct", 3, 1, 15, "Limit buy this % under the signal close"),
                Param("max_days", 10, 2, 30, "Time stop: closes held at most"),
                Param("top_n", 3, 1, 3, "Coins held at most"),
                Param("trend_days", 200, 50, 365, "Coin trend filter average, days"),
                Param("btc_filter", 0, 0, 1, "1: also need BTC above its trend_days average"),
                Param("min_history_days", 180, 30, 730, "Unbroken history needed"),
                Param("min_volume_musd", 20, 1, 500, "Median daily volume needed, million USDT"),
            ),
            universe=True,
        ),
        StrategySpec(
            "basket-breakout",
            "research",
            "Across liquid coins while BTC is above its trend_days average: buy a close above a "
            "band_days Bollinger band at entry_sd (rule 1) or 20% above the range_days low "
            "(rule 2); sell below the band at exit_sd or 20% under the high, or at a close "
            "below a stop_atr x ATR(20) stop. Calmest coins first, top_n slots. Research: runs "
            "only through its frozen note.",
            BasketBreakout,
            (
                Param("rule", 1, 1, 2, "1: Bollinger breakout, 2: 20% from the low"),
                Param("band_days", 100, 20, 200, "Rule 1: days in the band"),
                Param("entry_sd", 3, 1, 4, "Rule 1: standard deviations above to buy"),
                Param("exit_sd", 1, 0, 3, "Rule 1: standard deviations below to sell"),
                Param("range_days", 50, 10, 200, "Rule 2: days in the low and high"),
                Param("top_n", 3, 1, 3, "Coins held at most"),
                Param("stop_atr", 3, 1, 10, "Stop: ATRs below the entry close"),
                Param("trend_days", 200, 50, 365, "BTC trend filter average, days"),
                Param("vol_days", 63, 20, 252, "Days of volatility ranked (calmest first)"),
                Param("min_history_days", 180, 30, 730, "Unbroken history needed"),
                Param("min_volume_musd", 20, 1, 500, "Median daily volume needed, million USDT"),
            ),
            universe=True,
        ),
        StrategySpec(
            "monthly-rotation",
            "research",
            "At each month end, hold the top_n of BTC, ETH and SOL by lookback_days return "
            "among those above their trend_days average, 1/top_n each; cash for an empty slot. "
            "Research: runs only through its frozen note.",
            MonthlyRotation,
            (
                Param("lookback_days", 365, 30, 365, "Days of return ranked"),
                Param("trend_days", 200, 50, 365, "Trend filter average, days"),
                Param("top_n", 2, 1, 3, "Coins held"),
                Param("macd_filter", 0, 0, 1, "1: also need a daily MACD histogram above zero"),
            ),
            universe=True,
        ),
        StrategySpec(
            "eth-btc-value",
            "research",
            "Each Sunday, hold ETH when ETH/BTC is more than entry_tenths/10 standard deviations "
            "below its lookback_days average, BTC when as far above, else half each. Research: "
            "runs only through its frozen note.",
            EthBtcValue,
            (
                Param("lookback_days", 90, 20, 365, "Days in the ETH/BTC average"),
                Param("entry_tenths", 10, 5, 30, "Z-score to tilt fully, in tenths"),
            ),
            universe=True,
            assets=("BTCUSDT", "ETHUSDT"),
        ),
        StrategySpec(
            "btc-eth-half",
            "control",
            "Half BTC, half ETH, rebalanced each Sunday: the control for ETH/BTC value.",
            BtcEthHalf,
            universe=True,
            assets=("BTCUSDT", "ETHUSDT"),
        ),
        StrategySpec(
            "rotation-stablecoins",
            "research",
            "Monthly BTC/ETH/SOL rotation (top_n by lookback_days return, above the trend_days "
            "average) that holds cash at a month end when stablecoin supply shrank over 4 "
            "weeks. Research: runs only through its frozen note.",
            RotationWithStablecoins,
            (
                Param("lookback_days", 180, 30, 365, "Days of return ranked"),
                Param("trend_days", 200, 50, 365, "Trend filter average, days"),
                Param("top_n", 2, 1, 3, "Coins held"),
            ),
            universe=True,
            features=("stablecoins_up",),
        ),
        StrategySpec(
            "trend-trio",
            "research",
            "Trend-vol-target on BTC, ETH and SOL in one account, a third of equity each, sized "
            "inside its third exactly as alone. Research: runs only through its frozen note.",
            TrendTrio,
            (
                Param("fast_days", 20, 5, 60, "Fast trend lookback, days"),
                Param("mid_days", 60, 20, 180, "Middle trend lookback, days"),
                Param("slow_days", 120, 60, 365, "Slow trend lookback, days"),
                Param("vol_days", 30, 10, 90, "Days of volatility"),
                Param("target_vol_pct", 50, 10, 150, "Target annual volatility, %"),
            ),
            universe=True,
            assets=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        ),
        StrategySpec(
            "trio-hold",
            "control",
            "BTC, ETH and SOL held, a third each, rebalanced each Sunday: the control for "
            "trend-trio.",
            TrioHold,
            universe=True,
            assets=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        ),
        StrategySpec(
            "stablecoin-vol-hold",
            "research",
            "Each Sunday, hold the coin for the next week if stablecoin supply grew over 4 weeks; "
            "while in, size it by min(1, target_vol_pct / vol_days volatility). Research: runs "
            "only through its frozen note.",
            StablecoinVolHold,
            (
                Param("vol_days", 30, 10, 120, "Days of volatility"),
                Param("target_vol_pct", 50, 10, 150, "Target annual volatility, %"),
            ),
            features=("stablecoins_up",),
        ),
        StrategySpec(
            "trend-vol-target-stablecoin",
            "research",
            "Trend-vol-target with a fourth vote: stablecoin supply grew over 4 weeks. Research: "
            "runs only through its frozen note.",
            TrendVolTargetStablecoin,
            (
                Param("fast_days", 20, 5, 60, "Fast trend lookback, days"),
                Param("mid_days", 60, 20, 180, "Middle trend lookback, days"),
                Param("slow_days", 120, 60, 365, "Slow trend lookback, days"),
                Param("vol_days", 30, 10, 90, "Days of volatility"),
                Param("target_vol_pct", 50, 10, 150, "Target annual volatility, %"),
            ),
            features=("stablecoins_up",),
        ),
        StrategySpec(
            "stablecoin-hold",
            "research",
            "Each Sunday, hold the coin for the next week if stablecoin supply grew over the last "
            "4 weeks, else cash. Research: runs only through its frozen note.",
            StablecoinHold,
            features=("stablecoins_up",),
        ),
        StrategySpec(
            "btc-rotation-8",
            "research",
            "Each Sunday, while BTC closes margin_pct% above its btc_days average, hold the top "
            "top_n of 8 large coins by lookback_days return, each sized to target_vol_pct "
            "volatility; else cash. Exploration graduate G1: runs only through its frozen note.",
            BtcRegimeRotation,
            (
                Param("top_n", 3, 1, 8, "Coins held"),
                Param("lookback_days", 90, 30, 365, "Days of return ranked"),
                Param("btc_days", 50, 20, 200, "BTC's moving average, days"),
                Param("margin_pct", 3, 0, 10, "How far above its average BTC must close, %"),
                Param("vol_days", 30, 10, 90, "Days of returns in each coin's volatility"),
                Param("target_vol_pct", 50, 10, 100, "Annualised volatility target per slot, %"),
            ),
            universe=True,
            assets=LARGE_8,
        ),
        StrategySpec(
            "altcoin-rotation",
            "research",
            "Each Sunday, while BTC closes margin_pct% above its btc_days average, hold the top "
            "top_n altcoins by lookback_days return among the liquid ones then (min_volume_m "
            "million USDT a day, min_history days) that are above their close coin_days earlier, "
            "each sized to target_vol_pct volatility; sells a coin whose delisting is announced "
            "or whose first perp is under perp_exit_days old. Runs only through its frozen note.",
            AltcoinRegimeRotation,
            (
                Param("top_n", 3, 1, 8, "Coins held"),
                Param("lookback_days", 90, 30, 365, "Days of return ranked"),
                Param("btc_days", 50, 20, 200, "BTC's moving average, days"),
                Param("margin_pct", 3, 0, 10, "How far above its average BTC must close, %"),
                Param("coin_days", 30, 5, 120, "The coin's own trend, days"),
                Param("vol_days", 30, 10, 90, "Days of returns in each coin's volatility"),
                Param("target_vol_pct", 50, 10, 100, "Annualised volatility target per slot, %"),
                Param("min_volume_m", 20, 1, 500, "Median daily volume, million USDT"),
                Param("min_history", 365, 90, 1000, "Days of unbroken history"),
                Param("perp_exit_days", 30, 0, 90, "Avoid a coin this many days after its perp"),
            ),
            universe=True,
            coin_events=True,
        ),
        StrategySpec(
            "btc-rotation-4",
            "research",
            "Each Sunday, while BTC closes margin_pct% above its btc_days average, hold the top "
            "top_n of BTC/ETH/SOL/BNB by lookback_days return, sized to target_vol_pct "
            "volatility; else cash. Exploration graduate G2: runs only through its frozen note.",
            BtcRegimeRotation4,
            (
                Param("top_n", 1, 1, 4, "Coins held"),
                Param("lookback_days", 90, 30, 365, "Days of return ranked"),
                Param("btc_days", 50, 20, 200, "BTC's moving average, days"),
                Param("margin_pct", 3, 0, 10, "How far above its average BTC must close, %"),
                Param("vol_days", 30, 10, 90, "Days of returns in each coin's volatility"),
                Param("target_vol_pct", 50, 10, 100, "Annualised volatility target per slot, %"),
            ),
            universe=True,
            assets=LARGE_4,
        ),
        StrategySpec(
            "btc-rotation-8c",
            "research",
            "G1's rule on its confirmation basket (BTC and 7 large confirmation-half coins). "
            "Runs only through a frozen confirmation note's holdout test.",
            BtcRegimeRotationConfirm8,
            (
                Param("top_n", 3, 1, 8, "Coins held"),
                Param("lookback_days", 90, 30, 365, "Days of return ranked"),
                Param("btc_days", 50, 20, 200, "BTC's moving average, days"),
                Param("margin_pct", 3, 0, 10, "How far above its average BTC must close, %"),
                Param("vol_days", 30, 10, 90, "Days of returns in each coin's volatility"),
                Param("target_vol_pct", 50, 10, 100, "Annualised volatility target per slot, %"),
            ),
            universe=True,
            assets=CONFIRM_8,
        ),
        StrategySpec(
            "btc-rotation-4c",
            "research",
            "G2's rule on its confirmation basket (BTC and 3 large confirmation-half coins). "
            "Runs only through a frozen confirmation note's holdout test.",
            BtcRegimeRotationConfirm4,
            (
                Param("top_n", 1, 1, 4, "Coins held"),
                Param("lookback_days", 90, 30, 365, "Days of return ranked"),
                Param("btc_days", 50, 20, 200, "BTC's moving average, days"),
                Param("margin_pct", 3, 0, 10, "How far above its average BTC must close, %"),
                Param("vol_days", 30, 10, 90, "Days of returns in each coin's volatility"),
                Param("target_vol_pct", 50, 10, 100, "Annualised volatility target per slot, %"),
            ),
            universe=True,
            assets=CONFIRM_4,
        ),
        StrategySpec(
            "btc-gold-rotation",
            "research",
            "At each month end, hold whichever of BTC and gold (PAXG) has the better "
            "lookback_days return among those above their trend_days average; cash if neither. "
            "Research: runs only through its frozen note.",
            BtcGoldRotation,
            (
                Param("lookback_days", 365, 30, 365, "Days of return ranked"),
                Param("trend_days", 200, 50, 365, "Trend filter average, days"),
                Param("top_n", 1, 1, 2, "Assets held"),
                Param("macd_filter", 0, 0, 1, "1: also need a daily MACD histogram above zero"),
            ),
            universe=True,
            assets=("BTCUSDT", "PAXGUSDT"),
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
    limits = entries.filter(pl.col("limit").is_not_null())
    bad_limits = limits.filter(
        pl.col("limit").is_nan()
        | (pl.col("limit") <= pl.col("stop"))
        | (pl.col("limit") >= pl.col("target"))
        | pl.col("valid_bars").is_null()
        | (pl.col("valid_bars") < 1)
    )
    if bad_limits.height:
        raise ValueError(
            f"{strategy.name}: a limit entry needs stop < limit < target and valid_bars >= 1"
        )

    def compute(b: pl.DataFrame) -> pl.DataFrame:
        return _normalised_brackets(strategy.name, strategy.brackets(b))

    _check_prefixes(strategy.name, compute, bars, frame, checkpoints)
    return frame


def _normalised_brackets(name: str, frame: pl.DataFrame) -> pl.DataFrame:
    """Every bracket column, in schema order and type: an absent ``exit`` is False, an
    absent ``limit`` or ``valid_bars`` null (market entries)."""
    columns = set(frame.columns)
    required = set(BRACKET_SCHEMA) - OPTIONAL_BRACKET_COLUMNS
    if not required <= columns <= set(BRACKET_SCHEMA):
        raise ValueError(
            f"{name}: bracket columns must be {sorted(required)}, optionally "
            f"{sorted(OPTIONAL_BRACKET_COLUMNS)}"
        )
    if "exit" not in columns:
        frame = frame.with_columns(exit=pl.lit(False))
    for optional in ("limit", "valid_bars"):
        if optional not in columns:
            frame = frame.with_columns(pl.lit(None).alias(optional))
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
