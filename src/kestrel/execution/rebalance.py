"""One rebalance: how a target weight becomes at most one market order.

The backtest engine and the paper runner both call ``rebalance``, through the
``ExecutionVenue`` interface only, so a target means the same trade in both: the weight
held is measured at the reference price, a change no larger than the no-trade band is
skipped, a sale never sells more than is held, and a purchase is sized so its notional
plus costs fits in cash.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side
from kestrel.execution.venue import ExecutionVenue


@dataclass(frozen=True)
class Step:
    current: Decimal
    """Weight held before trading, at the reference price."""
    equity: Decimal
    """Cash plus the position at the reference price."""
    order: Fill | Rejection | None
    """None when the change was within the no-trade band."""


def rebalance(
    venue: ExecutionVenue,
    model: CostModel,
    symbol: str,
    target: Decimal,
    price: Decimal,
    last_volume: Decimal,
    band: Decimal | float,
    half_spread: Decimal | None = None,
) -> Step:
    """Move ``symbol`` towards ``target`` weight at ``price``, unless the change is within
    ``band``. ``last_volume`` (the last closed bar's quote volume) sizes the cost estimate."""
    held = venue.position(symbol)
    equity = venue.cash() + held * price
    current = held * price / equity
    if abs(target - current) <= Decimal(str(band)):
        return Step(current, equity, None)
    order = order_toward(
        venue, model, symbol, target, current, equity, price, last_volume, half_spread
    )
    return Step(current, equity, order)


def order_toward(
    venue: ExecutionVenue,
    model: CostModel,
    symbol: str,
    target: Decimal,
    current: Decimal,
    equity: Decimal,
    price: Decimal,
    last_volume: Decimal,
    half_spread: Decimal | None = None,
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
    rate = (
        model.cost_rate(Side.BUY, notional, last_volume, half_spread)
        if last_volume > 0
        else Decimal(0)
    )
    return venue.submit_market(symbol, Side.BUY, notional / (1 + rate) / price)
