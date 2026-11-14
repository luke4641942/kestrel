"""The execution venue: the only way a strategy's targets become trades.

``ExecutionVenue`` is the seam that keeps backtest, paper and live identical. The
engine (and later the paper and live runners) only ever call these methods; the
simulated venue below fills through the cost model, and P4 adds Binance testnet and
live implementations of the same interface.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

from kestrel.backtest.costs import CostModel, Fill, Rejection, Side
from kestrel.config import Config, InstrumentRules


class ExecutionVenue(Protocol):
    def cash(self) -> Decimal: ...

    def position(self, symbol: str) -> Decimal: ...

    def submit_market(self, symbol: str, side: Side, quantity: Decimal) -> Fill | Rejection: ...


class SimVenue:
    """Simulated spot account: cash in the account currency plus coin positions.

    The engine sets the market before each decision: the reference price (the bar's
    open), the last closed bar's quote volume, which sizes market impact, and optionally
    the coin's own half-spread. Exchange rules come from ``rules`` (by default each
    symbol's [instruments] entry, which must exist).
    """

    def __init__(
        self,
        config: Config,
        initial_cash: Decimal,
        rules: Callable[[str], InstrumentRules] | None = None,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial cash must be positive")
        self._config = config
        self._rules = rules or config.instrument
        self._model = CostModel(config.costs)
        self._cash = initial_cash
        self._positions: dict[str, Decimal] = {}
        self._market: dict[str, tuple[Decimal, Decimal, Decimal | None]] = {}

    def set_market(
        self,
        symbol: str,
        reference_price: Decimal,
        last_bar_volume: Decimal,
        half_spread: Decimal | None = None,
    ) -> None:
        self._market[symbol] = (reference_price, last_bar_volume, half_spread)

    def restore(self, cash: Decimal, positions: dict[str, Decimal]) -> None:
        """Resume an account from its ledger: the cash and coins it held."""
        if cash < 0 or any(q < 0 for q in positions.values()):
            raise ValueError("a spot account can't hold negative cash or coins")
        self._cash = cash
        self._positions = dict(positions)

    def holdings(self) -> dict[str, Decimal]:
        """Every coin held, by symbol (positions of zero dropped)."""
        return {s: q for s, q in self._positions.items() if q > 0}

    def cash(self) -> Decimal:
        return self._cash

    def position(self, symbol: str) -> Decimal:
        return self._positions.get(symbol, Decimal(0))

    def cost_model(self) -> CostModel:
        return self._model

    def submit_market(self, symbol: str, side: Side, quantity: Decimal) -> Fill | Rejection:
        if symbol not in self._market:
            raise RuntimeError(f"no market set for {symbol}; call set_market first")
        price, volume, half_spread = self._market[symbol]
        held = self.position(symbol)
        if side is Side.SELL and quantity > held:
            return Rejection(symbol, side, quantity, f"cannot sell {quantity}, holding {held}")
        result = self._model.fill_market(
            side, quantity, price, volume, self._rules(symbol), half_spread
        )
        if isinstance(result, Rejection):
            return result
        if self._cash + result.cash_flow < 0:
            return Rejection(
                symbol,
                side,
                quantity,
                f"insufficient cash: order needs {-result.cash_flow:.4f}, have {self._cash:.4f}",
            )
        self._cash += result.cash_flow
        signed = result.quantity if side is Side.BUY else -result.quantity
        self._positions[symbol] = held + signed
        return result
