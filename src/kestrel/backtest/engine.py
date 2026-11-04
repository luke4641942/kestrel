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

Bracket strategies (entries with a stop and a target) run a second loop, per bar:
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
from datetime import datetime
from decimal import Decimal
from typing import Literal

import polars as pl

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side
from kestrel.config import Config
from kestrel.data.schema import Interval
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
    """Open time of the bar the order filled at."""
    fill: Fill


@dataclass(frozen=True)
class RejectionRecord:
    time: datetime
    rejection: Rejection


ExitReason = Literal["target", "stop", "end of data"]


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
    target: Decimal
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
    config_fingerprint: str = ""
    code: CodeVersion | None = None

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
        log.warning("HOLDOUT USED: %s on %s from %s", strategy.name, strategy.symbol, holdout)

    symbol = strategy.symbol
    venue = SimVenue(config, initial_cash)
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
    )
    if isinstance(strategy, BracketStrategy):
        result.style = "brackets"
        rows = _run_brackets(strategy, bars, venue, config, result)
    else:
        rows = _run_weights(strategy, bars, venue, config, result)

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


Row = tuple[Decimal, Decimal, Decimal]
"""(equity, cash, position) at a bar's close."""


def _run_weights(
    strategy: Strategy,
    bars: pl.DataFrame,
    venue: SimVenue,
    config: Config,
    result: BacktestResult,
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
        venue.set_market(symbol, price, volumes[i - 1])
        held = venue.position(symbol)
        equity = venue.cash() + held * price
        current = held * price / equity
        if abs(target - current) > band:
            order = _rebalance(venue, model, symbol, target, current, equity, price, volumes[i - 1])
            if isinstance(order, Fill):
                result.fills.append(FillRecord(times[i], order))
            else:
                result.rejections.append(RejectionRecord(times[i], order))
        elif target != current and (i == 1 or target != targets[i - 2]):
            result.band_skips += 1
        held = venue.position(symbol)
        rows.append((venue.cash() + held * closes[i], venue.cash(), held))
    return rows


@dataclass
class _OpenTrade:
    entry: Fill
    entry_time: datetime
    stop: Decimal
    target: Decimal
    risk: Decimal
    capped: bool


def _run_brackets(
    strategy: BracketStrategy,
    bars: pl.DataFrame,
    venue: SimVenue,
    config: Config,
    result: BacktestResult,
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

    open_trade: _OpenTrade | None = None
    rows: list[Row] = [(result.initial_cash, result.initial_cash, Decimal(0))]
    for i in range(1, bars.height):
        o, h, lo, c = opens[i], highs[i], lows[i], closes[i]
        last_volume = volumes[i - 1]

        # 1. Entry at the open on the previous close's signal.
        if open_trade is None and enters[i - 1]:
            stop, target = stops[i - 1], targets[i - 1]
            assert stop is not None  # validated_brackets guarantees both on entry rows
            assert target is not None
            if not stop < o < target:
                result.rejections.append(
                    RejectionRecord(
                        times[i],
                        Rejection(
                            symbol,
                            Side.BUY,
                            Decimal(0),
                            f"opened at {o}, outside stop {stop} and target {target}; skipped",
                        ),
                    )
                )
            else:
                cash = venue.cash()
                wanted = risk_fraction * cash / (o - stop)
                rate = (
                    model.cost_rate(Side.BUY, min(wanted * o, cash), last_volume)
                    if last_volume > 0
                    else Decimal(0)
                )
                affordable = cash / (1 + rate) / o
                venue.set_market(symbol, o, last_volume)
                order = venue.submit_market(symbol, Side.BUY, min(wanted, affordable))
                if isinstance(order, Fill):
                    result.fills.append(FillRecord(times[i], order))
                    open_trade = _OpenTrade(
                        entry=order,
                        entry_time=times[i],
                        stop=stop,
                        target=target,
                        risk=order.quantity * (o - stop),
                        capped=affordable < wanted,
                    )
                else:
                    result.rejections.append(RejectionRecord(times[i], order))

        # 2. Exits inside the bar. The stop is checked first: when one bar reaches both
        # levels its order is unknown, so assume the worse outcome.
        if open_trade is not None:
            exit_at: Decimal | None = None
            reason: ExitReason = "stop"
            if lo <= open_trade.stop:
                exit_at = o if o <= open_trade.stop else open_trade.stop  # a gap fills at the open
            elif h >= open_trade.target:
                exit_at, reason = (o if o >= open_trade.target else open_trade.target), "target"
            if exit_at is not None:
                venue.set_market(symbol, exit_at, last_volume)
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
        venue.set_market(symbol, final, volumes[last])
        sell = venue.submit_market(symbol, Side.SELL, venue.position(symbol))
        if isinstance(sell, Fill):
            close_trade(open_trade, sell, times[last], "end of data")
            rows[-1] = (venue.cash(), venue.cash(), venue.position(symbol))
        else:
            result.rejections.append(RejectionRecord(times[last], sell))
    return rows


def _rebalance(
    venue: SimVenue,
    model: CostModel,
    symbol: str,
    target: Decimal,
    current: Decimal,
    equity: Decimal,
    price: Decimal,
    last_volume: Decimal,
) -> Fill | Rejection:
    """One market order moving the position from ``current`` to ``target`` weight."""
    if target < current:
        # current > target >= 0, so there is a position to sell.
        held = venue.position(symbol)
        qty = held if target == 0 else min(held, (current - target) * equity / price)
        return venue.submit_market(symbol, Side.SELL, qty)
    # Buying: size so notional plus all costs fits in cash. Costs depend on size, so
    # use the rate for the intended size (it only shrinks the order, never grows it).
    # Cash is positive whenever current < target (current = 1 - cash / equity).
    notional = min((target - current) * equity, venue.cash())
    rate = model.cost_rate(Side.BUY, notional, last_volume) if last_volume > 0 else Decimal(0)
    return venue.submit_market(symbol, Side.BUY, notional / (1 + rate) / price)
