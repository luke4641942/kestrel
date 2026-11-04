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
Data from the holdout period is refused unless the caller explicitly allows it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import polars as pl

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side
from kestrel.config import Config
from kestrel.data.schema import Interval
from kestrel.execution.venue import SimVenue
from kestrel.provenance import CodeVersion, code_version
from kestrel.strategy import Strategy, validated_weights

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
    strategy: Strategy,
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

    weights = validated_weights(strategy, bars)
    symbol = strategy.symbol
    venue = SimVenue(config, initial_cash)
    model = venue.cost_model()
    band = config.backtest.no_trade_band
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

    opens = [Decimal(str(v)) for v in bars["open"]]
    closes = [Decimal(str(v)) for v in bars["close"]]
    volumes = [Decimal(str(v)) for v in bars["quote_volume"]]
    times = bars["open_time"].to_list()
    targets = [Decimal(str(w)) for w in weights]

    rows: list[tuple[Decimal, Decimal, Decimal]] = [(initial_cash, initial_cash, Decimal(0))]
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
