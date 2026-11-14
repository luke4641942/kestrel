"""The shared rebalance step, through the ExecutionVenue interface alone: a fake venue with
nothing but cash, position and submit_market is enough, which is all a live venue offers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side
from kestrel.execution.rebalance import rebalance
from tests.backtest.helpers import config

D = Decimal
PRICE = D(100)
NONE = D(0)


class FakeVenue:
    """Fills at the reference price with no costs, and remembers every order."""

    def __init__(self, cash: Decimal, held: Decimal = NONE) -> None:
        self._cash, self._held = cash, held
        self.orders: list[tuple[Side, Decimal]] = []

    def cash(self) -> Decimal:
        return self._cash

    def position(self, symbol: str) -> Decimal:
        return self._held

    def submit_market(self, symbol: str, side: Side, quantity: Decimal) -> Fill | Rejection:
        self.orders.append((side, quantity))
        sign = 1 if side is Side.BUY else -1
        self._held += sign * quantity
        self._cash -= sign * quantity * PRICE
        return Fill(symbol, side, quantity, PRICE, PRICE, D(0), D(0), D(0))


FREE = CostModel(config(costs=False).costs)
REAL = CostModel(config().costs)


def step(venue: FakeVenue, target: str, model: CostModel = FREE, band: float = 0.02):  # type: ignore[no-untyped-def]
    return rebalance(venue, model, "BTCUSDT", D(target), PRICE, D(1e9), band)


def test_a_change_within_the_band_places_no_order() -> None:
    venue = FakeVenue(D(500), held=D(5))  # half in: 500 cash, 500 of coin
    s = step(venue, "0.51")
    assert (s.current, s.equity, s.order) == (D("0.5"), D(1000), None)
    assert venue.orders == []
    assert step(venue, "0.52").order is None  # exactly the band: still skipped


def test_buys_the_difference_and_sells_down_to_target() -> None:
    venue = FakeVenue(D(1000))
    s = step(venue, "0.6")
    assert isinstance(s.order, Fill)
    assert venue.orders == [(Side.BUY, D(6))]
    s = step(venue, "0.2")
    assert venue.orders[-1] == (Side.SELL, D(4))
    assert s.current == D("0.6")


def test_target_zero_sells_everything_held() -> None:
    venue = FakeVenue(D(0), held=D("3.3333"))
    step(venue, "0")
    assert venue.orders == [(Side.SELL, D("3.3333"))]  # all of it, not a rounded weight


def test_a_purchase_leaves_room_for_its_costs() -> None:
    venue = FakeVenue(D(1000))
    step(venue, "1", model=REAL)
    (side, qty) = venue.orders[0]
    assert side is Side.BUY
    rate = REAL.cost_rate(Side.BUY, D(1000), D(1e9), None)
    assert qty == pytest.approx(D(1000) / (1 + rate) / PRICE)
    assert qty * PRICE * (1 + rate) <= D(1000)
