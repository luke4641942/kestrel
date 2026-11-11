"""Transaction costs and simulated market-order fills.

Every market order pays three costs, each attributed separately so a backtest can
show where its returns went:

- **Fee**: the exchange's taker fee, charged on the executed notional.
- **Spread**: half the bid-ask spread, since a market buy lifts the ask and a
  market sell hits the bid.
- **Impact**: the price moving against a large order, by the square-root law:
  ``impact = coef * sqrt(participation)``, where participation is the order's
  notional over the quote volume of the last *closed* bar. Using the closed bar
  keeps this free of lookahead: a fill at bar t+1's open cannot know t+1's volume.

``fill_market`` (event-driven engine, one order at a time) and ``cost_rate``
(vectorised engine) share one formula, and a test asserts they agree exactly, so
the two engines cannot drift apart on costs.

Simplification: Binance charges a buyer's fee in the asset received. Charging the
equivalent value in the account currency is the same cost, and keeps one ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kestrel.config import CostConfig, InstrumentRules

_BPS = Decimal(10_000)


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: Side
    quantity: Decimal
    reference_price: Decimal
    """Price before costs: the next bar's open for an order decided at a bar close."""
    price: Decimal
    """Executed price after spread and impact."""
    fee: Decimal
    spread_cost: Decimal
    impact_cost: Decimal

    @property
    def total_cost(self) -> Decimal:
        return self.fee + self.spread_cost + self.impact_cost

    @property
    def cash_flow(self) -> Decimal:
        """Change in account-currency cash: negative for buys, positive for sells."""
        gross = self.quantity * self.price
        return -(gross + self.fee) if self.side is Side.BUY else gross - self.fee


@dataclass(frozen=True)
class Rejection:
    """An order the exchange (or a realistic market) would not have filled."""

    symbol: str
    side: Side
    requested_quantity: Decimal
    reason: str


class CostModel:
    def __init__(self, config: CostConfig) -> None:
        self.fee_rate = config.taker_fee_bps / _BPS
        self.half_spread_rate = config.half_spread_bps / _BPS
        self.impact_coef = config.impact_coef_bps / _BPS
        self.max_participation = config.max_participation

    def impact_rate(self, participation: Decimal) -> Decimal:
        return self.impact_coef * participation.sqrt()

    def cost_rate(
        self,
        side: Side,
        notional: Decimal,
        bar_quote_volume: Decimal,
        half_spread: Decimal | None = None,
    ) -> Decimal:
        """Total cost as a fraction of the order's notional at the reference price.
        ``half_spread`` (a fraction) overrides the configured one for this order.

        Buy:  pay  ref * (1 + slip) * (1 + fee)  per unit, so cost = (1+s)(1+f) - 1
        Sell: get  ref * (1 - slip) * (1 - fee)  per unit, so cost = 1 - (1-s)(1-f)
        """
        if bar_quote_volume <= 0:
            raise ValueError("bar_quote_volume must be positive; a zero-volume bar has no price")
        half = self.half_spread_rate if half_spread is None else half_spread
        slip = half + self.impact_rate(notional / bar_quote_volume)
        if side is Side.BUY:
            return (1 + slip) * (1 + self.fee_rate) - 1
        return 1 - (1 - slip) * (1 - self.fee_rate)

    def fill_market(
        self,
        side: Side,
        quantity: Decimal,
        reference_price: Decimal,
        bar_quote_volume: Decimal,
        rules: InstrumentRules,
        half_spread: Decimal | None = None,
    ) -> Fill | Rejection:
        """Simulate a market order. Quantity is rounded down to the exchange step.
        ``half_spread`` (a fraction) overrides the configured one, e.g. a coin's own
        estimated spread."""

        def reject(reason: str) -> Rejection:
            return Rejection(rules.symbol, side, quantity, reason)

        if quantity <= 0 or reference_price <= 0:
            return reject("quantity and price must be positive")
        qty = rules.round_quantity(quantity)
        if qty < rules.min_qty or qty == 0:
            return reject(f"quantity {qty} is below the minimum {rules.min_qty}")
        notional = qty * reference_price
        if notional < rules.min_notional:
            return reject(f"notional {notional:.2f} is below the minimum {rules.min_notional}")
        if bar_quote_volume <= 0:
            return reject("no traded volume in the last closed bar")
        participation = notional / bar_quote_volume
        if participation > self.max_participation:
            return reject(
                f"order is {participation:.2%} of the last bar's volume "
                f"(limit {self.max_participation:.2%})"
            )

        impact = self.impact_rate(participation)
        half = self.half_spread_rate if half_spread is None else half_spread
        slip = half + impact
        sign = 1 if side is Side.BUY else -1
        price = reference_price * (1 + sign * slip)
        return Fill(
            symbol=rules.symbol,
            side=side,
            quantity=qty,
            reference_price=reference_price,
            price=price,
            fee=qty * price * self.fee_rate,
            spread_cost=notional * half,
            impact_cost=notional * impact,
        )
