from __future__ import annotations

from decimal import Decimal

import pytest

from kestrel.backtest.costs import Fill, Rejection, Side
from kestrel.execution.venue import SimVenue
from tests.backtest.helpers import config

D = Decimal


@pytest.fixture
def venue() -> SimVenue:
    v = SimVenue(config(costs=False), D(1000))
    v.set_market("BTCUSDT", D(100), D(1e9))
    return v


def test_buy_moves_cash_into_the_position(venue: SimVenue) -> None:
    assert isinstance(venue.submit_market("BTCUSDT", Side.BUY, D(4)), Fill)
    assert venue.cash() == D(600)
    assert venue.position("BTCUSDT") == D(4)


def test_sell_returns_cash(venue: SimVenue) -> None:
    venue.submit_market("BTCUSDT", Side.BUY, D(4))
    venue.submit_market("BTCUSDT", Side.SELL, D(1.5))
    assert venue.cash() == D(750)
    assert venue.position("BTCUSDT") == D("2.5")


def test_cannot_overspend(venue: SimVenue) -> None:
    result = venue.submit_market("BTCUSDT", Side.BUY, D(11))
    assert isinstance(result, Rejection)
    assert "insufficient cash" in result.reason
    assert venue.cash() == D(1000)


def test_cannot_sell_what_it_does_not_hold(venue: SimVenue) -> None:
    result = venue.submit_market("BTCUSDT", Side.SELL, D(1))
    assert isinstance(result, Rejection)
    assert "holding 0" in result.reason


def test_market_must_be_set_first() -> None:
    with pytest.raises(RuntimeError, match="set_market"):
        SimVenue(config(), D(1000)).submit_market("BTCUSDT", Side.BUY, D(1))


def test_starting_cash_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        SimVenue(config(), D(0))
