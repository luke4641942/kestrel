"""Trades from a paper ledger: a coin from flat back to flat, resizes inside it."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from kestrel.execution.tracking import round_trips

D = Decimal


def fill(at: str, side: str, qty: str, price: str, fee: str = "0", **extra: str) -> dict[str, Any]:
    return {"type": "fill", "at": at, "side": side, "quantity": qty, "price": price, "fee": fee,
            **extra}  # fmt: skip


def state(bar: str, position: str) -> dict[str, Any]:
    return {"type": "state", "bar": bar, "cash": "0", "position": position}


def test_a_trade_runs_from_flat_to_flat_with_resizes_inside_it() -> None:
    events = [
        fill("d1", "buy", "1", "100", fee="0.1"), state("d0", "1"),
        fill("d2", "buy", "1", "110", fee="0.1"), state("d1", "2"),  # a resize
        fill("d3", "sell", "2", "120", fee="0.2"), state("d2", "0"),
        fill("d5", "buy", "1", "90"), state("d4", "1"),  # a second trade, still open
    ]  # fmt: skip
    first, second = round_trips(events, "BTCUSDT")
    assert (first.opened, first.closed, first.fills) == ("d1", "d3", 3)
    assert first.bought == D("210.2")
    assert first.sold == D("239.8")
    assert first.profit() == D("29.6")
    assert first.fees == D("0.4")
    assert second.closed is None
    assert second.held == 1
    assert second.profit() is None  # open: needs a price
    assert second.profit(D(95)) == D(5)


def test_basket_fills_are_tracked_per_coin_and_a_kill_closes_them() -> None:
    events = [
        fill("d1", "buy", "1", "10", symbol="SOLUSDT"),
        fill("d1", "buy", "2", "5", symbol="DOGEUSDT"),
        {"type": "state", "bar": "d0", "cash": "0", "positions": {"SOLUSDT": "1", "DOGEUSDT": "2"}},
        fill("d2", "sell", "2", "6", symbol="DOGEUSDT"),
        {"type": "state", "bar": "d1", "cash": "0", "positions": {"SOLUSDT": "1"}},
        {"type": "kill", "at": "d3", "sales": [
            {"symbol": "SOLUSDT", "side": "sell", "quantity": "1", "fill_price": "8"}]},
        {"type": "state", "bar": "d2", "cash": "0", "positions": {}},
    ]  # fmt: skip
    doge, sol = sorted(round_trips(events, "UNIVERSE"), key=lambda t: t.symbol)
    assert (doge.symbol, doge.closed, doge.profit()) == ("DOGEUSDT", "d2", D(2))
    assert (sol.symbol, sol.closed, sol.profit()) == ("SOLUSDT", "d3", D(-2))


def test_a_single_coin_kill_is_a_sell() -> None:
    events = [
        fill("d1", "buy", "1", "100"), state("d0", "1"),
        {"type": "kill", "at": "d2", "side": "sell", "quantity": "1", "fill_price": "90"},
        state("d1", "0"),
    ]  # fmt: skip
    (trip,) = round_trips(events, "BTCUSDT")
    assert (trip.closed, trip.profit()) == ("d2", D(-10))
    assert round_trips([], "BTCUSDT") == []
