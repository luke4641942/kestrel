"""Event-driven backtest engine: the reference implementation of how trades happen.

Timing (no lookahead):
    bar t closes  -> the strategy's weight for bar t is known
    bar t+1 opens -> orders to reach that weight fill at t+1's open, with market
                     impact sized from bar t's volume (the last *closed* bar)
    bar t+1 closes -> equity is marked at t+1's close

Orders go through an ``ExecutionVenue`` (here ``SimVenue``), never straight to a fill
function, so paper and live trading in P4 replace the venue and nothing else.

A rebalance only happens when the target differs from the current weight by more than
the no-trade band; skipped rebalances are counted so the band's effect is visible.

Bracket strategies (entries with a stop and usually a target) run a second loop, per
bar:
    0. An exit the strategy signalled at the previous close (e.g. a time exit) sells
       an open trade at this open.
    1. Entry at the open, if the previous close signalled and no trade is open. Size =
       risk_per_trade x equity / (open - stop), capped by cash (spot: no leverage).
    2. Exits inside the bar from its low and high. If a bar reaches both the stop and
       the target, the stop is assumed to have come first: bar data can't tell the
       order, so assume the worse one. If the bar opens past the stop (a gap), the exit
       is at the open, not the stop. Exits pay full taker costs.
    3. Equity is marked at the close. A trade still open at the end is closed at the
       final close so every trade has a result.
Data from the holdout period is refused unless the caller explicitly allows it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

import polars as pl

from kestrel.backtest.costs import Fill, Rejection, Side
from kestrel.backtest.spread import with_half_spread
from kestrel.config import Config
from kestrel.data.schema import Interval
from kestrel.execution.rebalance import rebalance
from kestrel.execution.venue import SimVenue
from kestrel.provenance import CodeVersion, code_version
from kestrel.strategy import (
    BracketStrategy,
    Strategy,
    validated_brackets,
    validated_weights,
)

log = logging.getLogger(__name__)


class HoldoutError(Exception):
    """The requested bars reach into the reserved holdout period."""


@dataclass(frozen=True)
class FillRecord:
    time: datetime
    """When the order filled: a bar's open, or the final bar's close for a trade closed
    at the end of the data."""
    fill: Fill


@dataclass(frozen=True)
class RejectionRecord:
    time: datetime
    rejection: Rejection


ExitReason = Literal["target", "stop", "signal", "end of data"]


@dataclass(frozen=True)
class TradeRecord:
    """One round trip of a bracket strategy, with its result in units of risk (R)."""

    entry_time: datetime
    exit_time: datetime
    exit_reason: ExitReason
    quantity: Decimal
    entry_price: Decimal
    """Executed, after spread and impact."""
    exit_price: Decimal
    stop: Decimal
    target: Decimal | None
    """None when the strategy set no target."""
    risk: Decimal
    """Planned loss at the stop before costs: quantity x (entry open - stop)."""
    pnl: Decimal
    """Net cash result after every cost."""
    costs: Decimal
    risk_capped: bool
    """True when cash couldn't fund the full risk, so the position is smaller."""

    @property
    def r_multiple(self) -> Decimal:
        return self.pnl / self.risk

    @property
    def cost_r(self) -> Decimal:
        return self.costs / self.risk


@dataclass(frozen=True)
class CostSummary:
    """Totals for an engine that computes costs per bar instead of per fill."""

    fees: float
    spread_costs: float
    impact_costs: float
    traded_notional: float
    rebalances: int


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    interval: Interval
    initial_cash: Decimal
    equity: pl.DataFrame
    """Per bar: close_time, equity, cash, position, weight (actual, after trading)."""
    fills: list[FillRecord] = field(default_factory=list)
    rejections: list[RejectionRecord] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    """Round trips; filled for bracket strategies only."""
    style: Literal["weights", "brackets"] = "weights"
    start: datetime | None = None
    """Open time of the first bar: when the starting cash is first marked."""
    band_skips: int = 0
    """New strategy decisions (target changed) not traded because the change was inside
    the no-trade band. Weight drift from price moves is not counted."""
    used_holdout: bool = False
    forced_exits: int = 0
    """Portfolio runs: coins sold at their last close because their bars stopped."""
    config_fingerprint: str = ""
    code: CodeVersion | None = None
    engine: Literal["event", "vectorised"] = "event"
    account_currency: str = "USDT"
    summary: CostSummary | None = None
    """Set by the vectorised engine, which has no individual fills to sum."""

    @property
    def asset(self) -> str:
        """What the benchmark holds: the symbol without its quote currency (ETHUSDT -> ETH)."""
        cur = self.account_currency
        return self.symbol[: -len(cur)] if cur and self.symbol.endswith(cur) else self.symbol

    @property
    def final_equity(self) -> Decimal:
        return Decimal(str(self.equity["equity"][-1])) if self.equity.height else self.initial_cash


def run_backtest(
    strategy: Strategy | BracketStrategy,
    bars: pl.DataFrame,
    interval: Interval,
    config: Config,
    initial_cash: Decimal,
    *,
    allow_holdout: bool = False,
) -> BacktestResult:
    """Run ``strategy`` over ``bars`` (canonical kline schema, one symbol, sorted)."""
    used_holdout = check_inputs(bars, config, strategy.name, strategy.symbol, allow_holdout)
    symbol = strategy.symbol
    venue = SimVenue(config, initial_cash, rules=config.rules_for)
    spreads = half_spreads(bars, config, symbol)
    result = BacktestResult(
        strategy=strategy.name,
        symbol=symbol,
        interval=interval,
        initial_cash=initial_cash,
        equity=pl.DataFrame(),
        start=bars["open_time"][0],
        used_holdout=used_holdout,
        config_fingerprint=config.fingerprint(),
        code=code_version(),
        account_currency=config.account_currency,
    )
    if isinstance(strategy, BracketStrategy):
        result.style = "brackets"
        rows = _run_brackets(strategy, bars, venue, config, result, spreads)
    else:
        rows = _run_weights(strategy, bars, venue, config, result, spreads)

    close_times = bars["open_time"] + interval.duration
    result.equity = pl.DataFrame(
        {
            "close_time": close_times,
            "equity": [float(r[0]) for r in rows],
            "cash": [float(r[1]) for r in rows],
            "position": [float(r[2]) for r in rows],
        }
    ).with_columns(
        weight=pl.when(pl.col("equity") > 0)
        .then(1 - pl.col("cash") / pl.col("equity"))
        .otherwise(0.0)
    )
    return result


def check_inputs(
    bars: pl.DataFrame, config: Config, name: str, symbol: str, allow_holdout: bool
) -> bool:
    """Checks every engine runs first. Returns whether the holdout is used."""
    if bars.height < 2:
        raise ValueError("need at least two bars: decide on one, fill on the next")
    if not bars["open_time"].is_sorted() or bars["open_time"].n_unique() != bars.height:
        raise ValueError("bars must be sorted with unique open_time; run the quality check")
    holdout = config.backtest.holdout_start
    used_holdout = bool(bars["open_time"].max() >= holdout)  # type: ignore[operator]
    if used_holdout and not allow_holdout:
        raise HoldoutError(
            f"bars reach into the holdout period (from {holdout:%Y-%m-%d} UTC). "
            "End the range before it, or pass allow_holdout for a finished strategy's "
            "one final test."
        )
    if used_holdout:
        log.warning("HOLDOUT USED: %s on %s from %s", name, symbol, holdout)
    return used_holdout


def half_spreads(bars: pl.DataFrame, config: Config, symbol: str) -> list[Decimal | None]:
    """The half-spread each bar's orders pay, known at that bar's close. None means the
    configured [costs] half_spread_bps: a coin with its own [instruments] entry (BTC, ETH)
    keeps it. Any other coin pays the band for its daily trading volume, as in the
    portfolio engine (kestrel.backtest.spread). The bands are set on daily volume, so on
    shorter bars a bar pays the band of the complete UTC days before its own (until
    2026-11-16 a window of bars was used instead, which on 5m bars read as a tiny coin)."""
    if symbol.upper() in config.instruments:
        return [None] * bars.height
    window = config.universe.volume_window_days
    tiers = [(float(v), float(bps) / 1e4) for v, bps in config.universe.spread_tiers]
    times = bars["open_time"]
    if bars.height < 2 or times[1] - times[0] >= timedelta(days=1):
        banded = with_half_spread(
            bars.select("quote_volume").with_columns(_coin=pl.lit(0)), window, tiers, ["_coin"]
        )
        return [Decimal(str(h)) for h in banded["half_spread"]]
    # Shorter bars: the bands are for daily volume, so each UTC day's bars sum to a day and
    # a bar pays the band of the days before its own (its day isn't complete yet).
    days = (
        bars.group_by(day=pl.col("open_time").dt.date())
        .agg(pl.col("quote_volume").sum())
        .sort("day")
        .with_columns(_coin=pl.lit(0))
    )
    banded_days = with_half_spread(days, window, tiers, ["_coin"]).select(
        "day", pl.col("half_spread").shift(1)
    )
    widest = max(spread for _, spread in tiers)
    joined = bars.select(day=pl.col("open_time").dt.date()).join(
        banded_days, on="day", how="left", maintain_order="left"
    )
    return [Decimal(str(widest if h is None else h)) for h in joined["half_spread"]]


Row = tuple[Decimal, Decimal, Decimal]
"""(equity, cash, position) at a bar's close."""


def _run_weights(
    strategy: Strategy,
    bars: pl.DataFrame,
    venue: SimVenue,
    config: Config,
    result: BacktestResult,
    spreads: list[Decimal | None],
) -> list[Row]:
    weights = validated_weights(strategy, bars)
    symbol = strategy.symbol
    model = venue.cost_model()
    band = config.backtest.no_trade_band
    opens = [Decimal(str(v)) for v in bars["open"]]
    closes = [Decimal(str(v)) for v in bars["close"]]
    volumes = [Decimal(str(v)) for v in bars["quote_volume"]]
    times = bars["open_time"].to_list()
    targets = [Decimal(str(w)) for w in weights]

    rows: list[Row] = [(result.initial_cash, result.initial_cash, Decimal(0))]
    for i in range(1, bars.height):
        price, target = opens[i], targets[i - 1]
        venue.set_market(symbol, price, volumes[i - 1], spreads[i - 1])
        step = rebalance(venue, model, symbol, target, price, volumes[i - 1], band, spreads[i - 1])
        if isinstance(step.order, Fill):
            result.fills.append(FillRecord(times[i], step.order))
        elif isinstance(step.order, Rejection):
            result.rejections.append(RejectionRecord(times[i], step.order))
        elif target != step.current and (i == 1 or target != targets[i - 2]):
            result.band_skips += 1
        held = venue.position(symbol)
        rows.append((venue.cash() + held * closes[i], venue.cash(), held))
    return rows


@dataclass
class _OpenTrade:
    entry: Fill
    entry_time: datetime
    stop: Decimal
    target: Decimal | None
    risk: Decimal
    capped: bool


def _run_brackets(
    strategy: BracketStrategy,
    bars: pl.DataFrame,
    venue: SimVenue,
    config: Config,
    result: BacktestResult,
    spreads: list[Decimal | None],
) -> list[Row]:
    frame = validated_brackets(strategy, bars)
    symbol = strategy.symbol
    model = venue.cost_model()
    risk_fraction = config.backtest.risk_per_trade

    def prices(col: str) -> list[Decimal]:
        return [Decimal(str(v)) for v in bars[col]]

    def levels(col: str) -> list[Decimal | None]:  # set on entry rows only
        return [None if v is None else Decimal(str(v)) for v in frame[col]]

    opens, highs, lows, closes = prices("open"), prices("high"), prices("low"), prices("close")
    volumes = prices("quote_volume")
    enters, stops, targets = frame["enter"].to_list(), levels("stop"), levels("target")
    exits, limits, valid = frame["exit"].to_list(), levels("limit"), frame["valid_bars"].to_list()
    times = bars["open_time"].to_list()

    def close_trade(trade: _OpenTrade, exit_fill: Fill, at: datetime, reason: ExitReason) -> None:
        result.fills.append(FillRecord(at, exit_fill))
        result.trades.append(
            TradeRecord(
                entry_time=trade.entry_time,
                exit_time=at,
                exit_reason=reason,
                quantity=trade.entry.quantity,
                entry_price=trade.entry.price,
                exit_price=exit_fill.price,
                stop=trade.stop,
                target=trade.target,
                risk=trade.risk,
                pnl=trade.entry.cash_flow + exit_fill.cash_flow,
                costs=trade.entry.total_cost + exit_fill.total_cost,
                risk_capped=trade.capped,
            )
        )

    def enter(
        i: int, price: Decimal, stop: Decimal, target: Decimal | None, what: str
    ) -> _OpenTrade | None:
        """Buy at ``price`` on bar ``i``, sized to risk the configured share of cash."""
        last_volume, last_spread = volumes[i - 1], spreads[i - 1]
        if not (stop < price and (target is None or price < target)):
            reason = (
                f"{what}, outside stop {stop} and target {target}; skipped"
                if target is not None
                else f"{what}, at or below stop {stop}; skipped"
            )
            result.rejections.append(
                RejectionRecord(times[i], Rejection(symbol, Side.BUY, Decimal(0), reason))
            )
            return None
        cash = venue.cash()
        wanted = risk_fraction * cash / (price - stop)
        rate = (
            model.cost_rate(Side.BUY, min(wanted * price, cash), last_volume, last_spread)
            if last_volume > 0
            else Decimal(0)
        )
        affordable = cash / (1 + rate) / price
        venue.set_market(symbol, price, last_volume, last_spread)
        order = venue.submit_market(symbol, Side.BUY, min(wanted, affordable))
        if not isinstance(order, Fill):
            result.rejections.append(RejectionRecord(times[i], order))
            return None
        result.fills.append(FillRecord(times[i], order))
        return _OpenTrade(
            entry=order,
            entry_time=times[i],
            stop=stop,
            target=target,
            risk=order.quantity * (price - stop),
            capped=affordable < wanted,
        )

    open_trade: _OpenTrade | None = None
    pending: tuple[Decimal, Decimal, Decimal | None, int] | None = None
    """A resting limit order: (limit, stop, target, the last bar it may fill on)."""
    rows: list[Row] = [(result.initial_cash, result.initial_cash, Decimal(0))]
    for i in range(1, bars.height):
        o, h, lo, c = opens[i], highs[i], lows[i], closes[i]
        last_volume, last_spread = volumes[i - 1], spreads[i - 1]

        # 0. An exit signalled at the previous close sells at this open.
        if open_trade is not None and exits[i - 1]:
            venue.set_market(symbol, o, last_volume, last_spread)
            sell = venue.submit_market(symbol, Side.SELL, venue.position(symbol))
            if isinstance(sell, Fill):
                close_trade(open_trade, sell, times[i], "signal")
                open_trade = None
            else:
                result.rejections.append(RejectionRecord(times[i], sell))

        # 1. Entry on the previous close's signal: a market order fills at this open; a
        # limit order rests (replacing an older one) and fills when a bar reaches it.
        filled_on_limit = False
        if open_trade is None and enters[i - 1]:
            stop, target = stops[i - 1], targets[i - 1]
            assert stop is not None  # validated_brackets guarantees a stop on entry rows
            limit = limits[i - 1]
            if limit is not None:
                pending = (limit, stop, target, i - 1 + int(valid[i - 1]))
            else:
                pending = None
                open_trade = enter(i, o, stop, target, f"opened at {o}")
        if open_trade is None and pending is not None:
            limit, stop, target, last_bar = pending
            if i > last_bar:
                pending = None
            elif lo <= limit:
                pending = None
                price = min(o, limit)
                open_trade = enter(i, price, stop, target, f"limit filled at {price}")
                filled_on_limit = open_trade is not None

        # 2. Exits inside the bar. The stop is checked first: when one bar reaches both
        # levels its order is unknown, so assume the worse outcome.
        if open_trade is not None:
            exit_at: Decimal | None = None
            reason: ExitReason = "stop"
            if lo <= open_trade.stop:
                gapped = o <= open_trade.stop and not filled_on_limit
                exit_at = o if gapped else open_trade.stop  # a gap fills at the open
            elif open_trade.target is not None and h >= open_trade.target and not filled_on_limit:
                exit_at, reason = (o if o >= open_trade.target else open_trade.target), "target"
            if exit_at is not None:
                venue.set_market(symbol, exit_at, last_volume, last_spread)
                sell = venue.submit_market(symbol, Side.SELL, venue.position(symbol))
                if isinstance(sell, Fill):
                    close_trade(open_trade, sell, times[i], reason)
                    open_trade = None
                else:  # e.g. the position fell below the exchange minimum; keep it
                    result.rejections.append(RejectionRecord(times[i], sell))

        held = venue.position(symbol)
        rows.append((venue.cash() + held * c, venue.cash(), held))

    # 3. Close anything still open at the final close, so every trade has a result.
    if open_trade is not None:
        last = bars.height - 1
        final = closes[last]
        closed_at = times[last] + result.interval.duration  # sold at the close, not the open
        venue.set_market(symbol, final, volumes[last], spreads[last])
        sell = venue.submit_market(symbol, Side.SELL, venue.position(symbol))
        if isinstance(sell, Fill):
            close_trade(open_trade, sell, closed_at, "end of data")
            rows[-1] = (venue.cash(), venue.cash(), venue.position(symbol))
        else:
            result.rejections.append(RejectionRecord(closed_at, sell))
    return rows
