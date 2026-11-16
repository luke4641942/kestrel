"""The Binance venue, offline against a fake that checks signatures as Binance does, plus a
network test against the real Spot Testnet (needs KESTREL_TESTNET_KEY / _SECRET)."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side
from kestrel.data.schema import Interval
from kestrel.execution.binance import (
    TESTNET_URL,
    BinanceClient,
    BinanceError,
    BinanceVenue,
    Credentials,
)
from kestrel.execution.paper import PaperAccount, Terms, run
from tests.backtest.helpers import T0, bars, config

D = Decimal
CREDS = Credentials("key-123", "secret-456")
SERVER_MS = 1_700_000_000_000
LOCAL_S = SERVER_MS / 1000 - 3.0  # the local clock runs 3 seconds slow


class FakeBinance:
    def __init__(self) -> None:
        self.orders: list[dict[str, str]] = []
        self.reply: tuple[int, dict[str, Any] | str] | None = None
        self.commission = ("0.00001", "BTC")
        self.notional_filter = "NOTIONAL"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": SERVER_MS})
        if path == "/api/v3/exchangeInfo":
            filters = [
                {"filterType": "LOT_SIZE", "minQty": "0.00001000", "stepSize": "0.00001000"},
                {"filterType": self.notional_filter, "minNotional": "5.00000000"},
            ]
            return httpx.Response(200, json={"symbols": [{"filters": filters}]})
        # Signed endpoints: check the signature over the query exactly as Binance does.
        query = request.url.query.decode()
        body, _, signature = query.rpartition("&signature=")
        expected = hmac.new(CREDS.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        params = dict(parse_qsl(body))
        if request.headers.get("X-MBX-APIKEY") != CREDS.key or signature != expected:
            return httpx.Response(
                401, json={"code": -1022, "msg": "Signature for this request is not valid."}
            )
        assert int(params["timestamp"]) == SERVER_MS  # the server's clock, not the local one
        if path == "/api/v3/account":
            return httpx.Response(200, json={"balances": [
                {"asset": "BTC", "free": "1.00000000", "locked": "0"},
                {"asset": "USDT", "free": "10000.00000000", "locked": "0"},
                {"asset": "ETH", "free": "0.00000000", "locked": "0"},
            ]})  # fmt: skip
        assert path == "/api/v3/order"
        self.orders.append(params)
        if self.reply:
            status, payload = self.reply
            if isinstance(payload, str):  # a proxy's error page, not Binance's JSON
                return httpx.Response(status, text=payload)
            return httpx.Response(status, json=payload)
        qty = D(params["quantity"])
        fee, asset = self.commission
        return httpx.Response(200, json={
            "orderId": 7, "status": "FILLED", "executedQty": str(qty),
            "cummulativeQuoteQty": str(qty * D("100.5")),
            "fills": [
                {"price": "100.5", "qty": str(qty), "commission": fee, "commissionAsset": asset}
            ],
        })  # fmt: skip

    def client(self) -> BinanceClient:
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        return BinanceClient(CREDS, http, TESTNET_URL, clock=lambda: LOCAL_S)


def venue(fake: FakeBinance, cash: str = "330", position: str = "0") -> BinanceVenue:
    v = BinanceVenue(
        fake.client(), "BTCUSDT", "USDT", CostModel(config().costs), D(cash), D(position)
    )
    v.set_market("BTCUSDT", D(100), D(1e9))
    return v


def test_rules_and_balances_come_from_the_exchange() -> None:
    fake = FakeBinance()
    client = fake.client()
    rules = client.rules("BTCUSDT")
    assert (rules.step_size, rules.min_qty, rules.min_notional) == (
        D("0.00001"),
        D("0.00001"),
        D(5),
    )
    fake.notional_filter = "MIN_NOTIONAL"  # older symbols still use this name
    assert fake.client().rules("BTCUSDT").min_notional == D(5)
    assert client.balances() == {"BTC": D(1), "USDT": D(10000)}  # empty assets left out


def test_a_buy_is_rounded_down_signed_and_accounted_from_the_real_fill() -> None:
    fake = FakeBinance()
    v = venue(fake)
    fill = v.submit_market("BTCUSDT", Side.BUY, D("1.234567891"))
    assert fake.orders[0]["quantity"] == "1.23456"  # down to the step, never up
    assert (fake.orders[0]["side"], fake.orders[0]["type"]) == ("BUY", "MARKET")
    assert isinstance(fill, Fill)
    assert (fill.quantity, fill.price, fill.reference_price) == (D("1.23456"), D("100.5"), D(100))
    assert fill.fee == D("0.00001") * D("100.5")  # the BTC commission, valued in USDT
    assert fill.spread_cost == D("0.5") * D("1.23456")  # paid 0.5 over the reference
    assert v.position("BTCUSDT") == D("1.23456") - D("0.00001")  # commission came out of the coin
    assert v.cash() == D(330) - D("1.23456") * D("100.5")
    assert v.position("ETHUSDT") == 0


def test_a_sell_pays_its_commission_in_the_quote() -> None:
    fake = FakeBinance()
    fake.commission = ("0.10", "USDT")
    v = venue(fake, cash="0", position="2")
    fill = v.submit_market("BTCUSDT", Side.SELL, D(2))
    assert isinstance(fill, Fill)
    assert v.cash() == D(2) * D("100.5") - D("0.10")
    assert v.position("BTCUSDT") == 0
    assert fill.spread_cost == D("-0.5") * 2  # sold above the reference: negative slippage


@pytest.mark.parametrize(
    ("side", "qty", "cash", "position", "reason"),
    [
        (Side.BUY, "0.000001", "330", "0", "below the minimum 0.00001"),
        (Side.BUY, "0.04", "330", "0", "notional 4.00 is below the minimum 5"),
        (Side.BUY, "4", "330", "0", "the allocation has 330.00"),
        (Side.SELL, "1", "0", "0.5", "the allocation holds 0.5"),
    ],
)
def test_orders_are_checked_before_anything_is_sent(
    side: Side, qty: str, cash: str, position: str, reason: str
) -> None:
    fake = FakeBinance()
    result = venue(fake, cash, position).submit_market("BTCUSDT", side, D(qty))
    assert isinstance(result, Rejection)
    assert reason in result.reason
    assert fake.orders == []


def test_binance_refusing_the_order_is_a_rejection_anything_else_stops() -> None:
    fake = FakeBinance()
    fake.reply = (
        400,
        {"code": -2010, "msg": "Account has insufficient balance for requested action."},
    )
    refused = venue(fake).submit_market("BTCUSDT", Side.BUY, D(1))
    assert isinstance(refused, Rejection)
    assert "insufficient balance" in refused.reason
    fake.reply = (
        400,
        {"code": -1021, "msg": "Timestamp for this request is outside of the recvWindow."},
    )
    with pytest.raises(BinanceError, match="recvWindow"):
        venue(fake).submit_market("BTCUSDT", Side.BUY, D(1))
    fake.reply = (502, {})
    with pytest.raises(BinanceError, match="502"):
        venue(fake).submit_market("BTCUSDT", Side.BUY, D(1))


def test_wrong_keys_and_odd_fills_stop_rather_than_guess() -> None:
    fake = FakeBinance()
    wrong = BinanceClient(
        Credentials("key-123", "nope"),
        httpx.Client(transport=httpx.MockTransport(fake.handler)),
        clock=lambda: LOCAL_S,
    )
    with pytest.raises(BinanceError, match="Signature"):
        wrong.balances()
    fake.commission = ("0.01", "BNB")
    with pytest.raises(BinanceError, match="commission charged in BNB"):
        venue(fake).submit_market("BTCUSDT", Side.BUY, D(1))
    fake.reply = (
        200,
        {"orderId": 8, "status": "EXPIRED", "executedQty": "0", "cummulativeQuoteQty": "0"},
    )
    with pytest.raises(BinanceError, match="filled nothing"):
        venue(fake).submit_market("BTCUSDT", Side.BUY, D(1))


def test_misuse_is_refused() -> None:
    fake = FakeBinance()
    with pytest.raises(ValueError, match="isn't quoted in USDT"):
        BinanceVenue(fake.client(), "BTCEUR", "USDT", CostModel(config().costs), D(1))
    bare = BinanceVenue(fake.client(), "BTCUSDT", "USDT", CostModel(config().costs), D(330))
    with pytest.raises(RuntimeError, match="set_market"):
        bare.submit_market("BTCUSDT", Side.BUY, D(1))
    with pytest.raises(ValueError, match="trades BTCUSDT"):
        venue(fake).submit_market("ETHUSDT", Side.BUY, D(1))
    with pytest.raises(ValueError, match="negative"):
        bare.restore(D(-1), {})
    bare.restore(D(50), {"BTCUSDT": D(2)})
    assert (bare.cash(), bare.position("BTCUSDT"), bare.balances()["BTC"]) == (D(50), D(2), D(1))
    assert bare.cost_model() is not None


def test_credentials_come_from_the_environment_and_never_print(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KESTREL_TESTNET_KEY", raising=False)
    with pytest.raises(BinanceError, match="KESTREL_TESTNET_KEY"):
        Credentials.from_env()
    monkeypatch.setenv("KESTREL_TESTNET_KEY", "k")
    monkeypatch.setenv("KESTREL_TESTNET_SECRET", "very-secret")
    creds = Credentials.from_env()
    assert creds.secret == "very-secret"
    assert "very-secret" not in repr(creds)


def test_a_live_venue_behind_records_missed_days_and_trades_the_latest(tmp_path: Path) -> None:
    frame = bars([100.0 + i for i in range(40)], step=timedelta(days=1))
    behind = PaperAccount.open(
        tmp_path / "a",
        Terms(
            "a",
            "ma-trend",
            "BTCUSDT",
            Interval.D1,
            {"fast": 5, "slow": 20},
            D(330),
            T0,
            venue="testnet",
        ),
    )
    fake_behind = FakeBinance()
    caught_up = run(behind, frame, config(), lambda _: D(100), venue=venue(fake_behind))
    assert [h.missed for h in caught_up] == [True] * 39 + [False]  # only the latest trades
    assert len(fake_behind.orders) == 1
    missed = [e for e in behind.ledger.events() if e.get("missed")]
    assert len(missed) == 39
    assert missed[0]["price"] is None
    fake = FakeBinance()
    latest = frame["open_time"][-1]
    current = PaperAccount.open(
        tmp_path / "b",
        Terms(
            "b",
            "ma-trend",
            "BTCUSDT",
            Interval.D1,
            {"fast": 5, "slow": 20},
            D(330),
            latest,
            venue="testnet",
        ),
    )
    assert current.terms().venue == "testnet"
    (handled,) = run(current, frame, config(), lambda _: D(100), venue=venue(fake))
    assert isinstance(handled.order, Fill)  # a rising market: fully in
    assert fake.orders
    assert fake.orders[0]["side"] == "BUY"
    state = current.state()
    assert state.cash < D(330)
    assert state.position > 0


@pytest.mark.network
@pytest.mark.skipif(not os.environ.get("KESTREL_TESTNET_KEY"), reason="no testnet keys")
def test_the_real_testnet() -> None:
    with httpx.Client(timeout=20) as http:
        client = BinanceClient(Credentials.from_env(), http)
        rules = client.rules("BTCUSDT")
        assert rules.step_size > 0
        assert rules.min_notional > 0
        assert "USDT" in client.balances()


def test_the_edges() -> None:
    fake = FakeBinance()
    fake.reply = (502, "<html>Bad Gateway</html>")
    with pytest.raises(BinanceError, match="Binance 502: <html>Bad Gateway"):
        venue(fake).submit_market("BTCUSDT", Side.BUY, D(1))
    fake = FakeBinance()
    fake.commission = ("0", "BNB")  # a zero commission in another asset moves nothing
    fill = venue(fake).submit_market("BTCUSDT", Side.BUY, D(1))
    assert isinstance(fill, Fill)
    assert fill.fee == 0
    client = fake.client()
    client.balances()
    client.balances()  # the server's clock is read once, then reused


def test_a_basket_venue_trades_and_accounts_each_coin_separately() -> None:
    fake = FakeBinance()
    fake.commission = ("0.01", "USDT")
    v = BinanceVenue(
        fake.client(), ["BTCUSDT", "ETHUSDT"], "USDT", CostModel(config().costs), D(330)
    )
    for s in v.symbols:
        v.set_market(s, D(100), D(1e9))
    assert isinstance(v.submit_market("BTCUSDT", Side.BUY, D(1)), Fill)
    assert isinstance(v.submit_market("ETHUSDT", Side.BUY, D("0.5")), Fill)
    assert v.holdings() == {"BTCUSDT": D(1), "ETHUSDT": D("0.5")}
    assert [o["symbol"] for o in fake.orders] == ["BTCUSDT", "ETHUSDT"]
    assert v.cash() == D(330) - D("150.75") - D("0.02")  # 1.5 coins at 100.5, two fees
    assert v.assets(v.cash(), v.holdings()) == [
        ("BTC", D(1)),
        ("ETH", D("0.5")),
        ("USDT", v.cash()),
    ]
    with pytest.raises(ValueError, match="BTCUSDT, ETHUSDT, not SOLUSDT"):
        v.submit_market("SOLUSDT", Side.BUY, D(1))
    with pytest.raises(ValueError, match="doesn't trade"):
        v.restore(D(1), {"SOLUSDT": D(1)})
    with pytest.raises(ValueError, match="restored from its ledger"):
        BinanceVenue(fake.client(), ["BTCUSDT", "ETHUSDT"], "USDT", CostModel(config().costs),
                     D(1), D(1))  # fmt: skip
