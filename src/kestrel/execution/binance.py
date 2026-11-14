"""Binance spot as an ExecutionVenue: the Spot Testnet now, live later, the same code.

A paper account trades its own allocation (e.g. 330 USDT), not everything in the exchange
account: Binance's testnet hands out thousands of fake USDT and BTC. So the venue keeps the
allocation's cash and coin, resumed from the ledger, and changes them only by what each
order really filled: the quote spent or received and the commission, in whichever asset
Binance charged it. ``balances()`` reads the whole exchange account for reconciliation
(P4 S4), which checks the exchange holds at least what every allocation says it does.

Orders are market orders sized in the base asset, rounded down to the symbol's LOT_SIZE
step and checked against its minimum quantity and notional (read from exchangeInfo, not
assumed) before anything is sent. An order Binance refuses (insufficient balance, a filter)
comes back as a Rejection with Binance's own message; anything else (bad keys, the clock,
the network) raises, because trading on regardless would be unsafe.

The reported Fill keeps the backtest's shape: ``reference_price`` is the price the decision
was sized at (the bar's open), ``price`` the volume-weighted fill price, ``fee`` the
commission valued in the quote asset, and ``spread_cost`` the measured slippage against the
reference (spread and impact together; a real fill can't separate them). ``impact_cost``
is 0.

Keys come from the environment (``KESTREL_TESTNET_KEY`` / ``KESTREL_TESTNET_SECRET``) and
are never logged, written to the ledger, or sent anywhere but Binance.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side
from kestrel.config import InstrumentRules

TESTNET_URL = "https://testnet.binance.vision"
KEY_ENV, SECRET_ENV = "KESTREL_TESTNET_KEY", "KESTREL_TESTNET_SECRET"
RECV_WINDOW = 5000


class BinanceError(Exception):
    """A request Binance refused for a reason other than the order itself."""


@dataclass(frozen=True)
class Credentials:
    key: str
    secret: str

    @classmethod
    def from_env(cls) -> Credentials:
        key, secret = os.environ.get(KEY_ENV, ""), os.environ.get(SECRET_ENV, "")
        if not key or not secret:
            raise BinanceError(
                f"set {KEY_ENV} and {SECRET_ENV} (Binance Spot Testnet keys) in your "
                "environment, e.g. in ~/.zshrc"
            )
        return cls(key, secret)

    def __repr__(self) -> str:  # never show the secret in a log or a traceback
        return "Credentials(key=…, secret=…)"


class BinanceClient:
    """Signed REST calls. The server's clock is read once, so a Mac clock a few seconds out
    doesn't get every signed request refused."""

    def __init__(
        self,
        credentials: Credentials,
        http: httpx.Client,
        base_url: str = TESTNET_URL,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._credentials = credentials
        self._http = http
        self._base = base_url.rstrip("/")
        self._clock = clock
        self._offset_ms: int | None = None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._check(self._http.get(f"{self._base}{path}", params=params))

    def _now_ms(self) -> int:
        if self._offset_ms is None:
            server = int(self._get("/api/v3/time")["serverTime"])
            self._offset_ms = server - int(self._clock() * 1000)
        return int(self._clock() * 1000) + self._offset_ms

    def signed(self, method: str, path: str, params: dict[str, Any]) -> httpx.Response:
        query = urlencode({**params, "recvWindow": RECV_WINDOW, "timestamp": self._now_ms()})
        signature = hmac.new(
            self._credentials.secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return self._http.request(
            method,
            f"{self._base}{path}?{query}&signature={signature}",
            headers={"X-MBX-APIKEY": self._credentials.key},
        )

    @staticmethod
    def _check(response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise BinanceError(_message(response))
        return response.json()

    def rules(self, symbol: str) -> InstrumentRules:
        info = self._get("/api/v3/exchangeInfo", {"symbol": symbol})
        filters = {f["filterType"]: f for f in info["symbols"][0]["filters"]}
        lot = filters["LOT_SIZE"]
        notional = filters.get("NOTIONAL") or filters["MIN_NOTIONAL"]
        return InstrumentRules(
            symbol,
            Decimal(lot["stepSize"]).normalize(),
            Decimal(lot["minQty"]).normalize(),
            Decimal(notional["minNotional"]).normalize(),
        )

    def balances(self) -> dict[str, Decimal]:
        """Free balance of every asset the exchange account holds (non-zero only)."""
        account = self._check(self.signed("GET", "/api/v3/account", {}))
        return {
            b["asset"]: Decimal(b["free"]) for b in account["balances"] if Decimal(b["free"]) > 0
        }


def _code(response: httpx.Response) -> int | None:
    try:
        code = response.json().get("code")
    except ValueError:
        return None
    return code if isinstance(code, int) else None


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
        return f"Binance {response.status_code} ({body.get('code')}): {body.get('msg')}"
    except ValueError:
        return f"Binance {response.status_code}: {response.text[:200]}"


ORDER_REFUSALS = {-1013, -2010}
"""Binance codes for an order it won't fill (a filter; insufficient balance): a Rejection,
like the simulator's. Every other error raises."""


class BinanceVenue:
    """An allocation of a Binance spot account, trading one symbol."""

    live = True
    """Orders fill now, at the market: a decision can't be traded at a past open."""

    def __init__(
        self,
        client: BinanceClient,
        symbol: str,
        quote: str,
        cost_model: CostModel,
        cash: Decimal,
        position: Decimal = Decimal(0),
    ) -> None:
        if not symbol.endswith(quote):
            raise ValueError(f"{symbol} isn't quoted in {quote}")
        self._client = client
        self.symbol = symbol
        self.base, self.quote = symbol[: -len(quote)], quote
        self._model = cost_model
        self._cash, self._position = cash, position
        self._rules = client.rules(symbol)
        self._reference: Decimal | None = None

    # The paper runner's venue interface, as SimVenue offers it.
    def set_market(
        self,
        symbol: str,
        reference_price: Decimal,
        last_bar_volume: Decimal,
        half_spread: Decimal | None = None,
    ) -> None:
        self._reference = reference_price

    def restore(self, cash: Decimal, positions: dict[str, Decimal]) -> None:
        if cash < 0 or any(q < 0 for q in positions.values()):
            raise ValueError("a spot account can't hold negative cash or coins")
        self._cash, self._position = cash, positions.get(self.symbol, Decimal(0))

    def cost_model(self) -> CostModel:
        return self._model

    def balances(self) -> dict[str, Decimal]:
        return self._client.balances()

    # ExecutionVenue
    def cash(self) -> Decimal:
        return self._cash

    def position(self, symbol: str) -> Decimal:
        return self._position if symbol == self.symbol else Decimal(0)

    def submit_market(self, symbol: str, side: Side, quantity: Decimal) -> Fill | Rejection:
        if symbol != self.symbol:
            raise ValueError(f"this venue trades {self.symbol}, not {symbol}")
        if self._reference is None:
            raise RuntimeError("no reference price set; call set_market first")
        rules = self._rules
        qty = rules.round_quantity(quantity)

        def reject(reason: str) -> Rejection:
            return Rejection(symbol, side, quantity, reason)

        if side is Side.SELL and qty > self._position:
            return reject(f"cannot sell {qty}, the allocation holds {self._position}")
        if qty < rules.min_qty or qty == 0:
            return reject(f"quantity {qty} is below the minimum {rules.min_qty}")
        if qty * self._reference < rules.min_notional:
            return reject(
                f"notional {qty * self._reference:.2f} is below the minimum {rules.min_notional}"
            )
        if side is Side.BUY and qty * self._reference > self._cash:
            return reject(
                f"needs about {qty * self._reference:.2f}, the allocation has {self._cash:.2f}"
            )
        response = self._client.signed(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": side.value.upper(),
                "type": "MARKET",
                "quantity": format(qty, "f"),
                "newOrderRespType": "FULL",
            },
        )
        if response.status_code >= 400:
            if _code(response) in ORDER_REFUSALS:
                return reject(_message(response))
            raise BinanceError(_message(response))
        return self._record(side, response.json(), self._reference)

    def _record(self, side: Side, order: dict[str, Any], reference: Decimal) -> Fill:
        filled = Decimal(order["executedQty"])
        quote = Decimal(order["cummulativeQuoteQty"])
        if filled <= 0:
            raise BinanceError(
                f"order {order.get('orderId')} filled nothing ({order.get('status')})"
            )
        price = quote / filled
        fee_in_quote = Decimal(0)
        base_fee = quote_fee = Decimal(0)
        for f in order.get("fills", []):
            commission = Decimal(f["commission"])
            if f["commissionAsset"] == self.base:
                base_fee += commission
                fee_in_quote += commission * Decimal(f["price"])
            elif f["commissionAsset"] == self.quote:
                quote_fee += commission
                fee_in_quote += commission
            elif commission > 0:  # BNB or similar: not ours to move, but reported
                raise BinanceError(
                    f"commission charged in {f['commissionAsset']}; this venue only "
                    f"accounts for {self.base} and {self.quote}"
                )
        if side is Side.BUY:
            self._cash -= quote + quote_fee
            self._position += filled - base_fee
            slippage = (price - reference) * filled
        else:
            self._cash += quote - quote_fee
            self._position -= filled + base_fee
            slippage = (reference - price) * filled
        return Fill(
            self.symbol,
            side,
            filled,
            reference,
            price,
            fee_in_quote,
            slippage,
            Decimal(0),
        )
