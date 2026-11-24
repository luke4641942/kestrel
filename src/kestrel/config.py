"""Platform configuration, loaded from ``kestrel.toml``.

The file is the single source of truth: there are no defaults in code, and unknown
keys are errors, so a typo cannot silently fall back to some other value. Numbers
that feed money calculations are parsed as ``Decimal``.

A ``Config`` keeps the parsed document so a backtest run can store exactly what it
ran with, and ``fingerprint()`` lets runs with identical assumptions be grouped.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from kestrel.exploration import SPLIT_FILE, ExplorationError, Split, load_split


class ConfigError(ValueError):
    """The config file is missing, malformed or has an invalid value."""


@dataclass(frozen=True)
class CostConfig:
    taker_fee_bps: Decimal
    half_spread_bps: Decimal
    impact_coef_bps: Decimal
    max_participation: Decimal


@dataclass(frozen=True)
class BacktestConfig:
    no_trade_band: Decimal
    risk_per_trade: Decimal
    """Fraction of equity risked per trade by strategies with stops."""
    holdout_start: datetime
    """First instant of the holdout period (UTC). Research must stay before it."""


@dataclass(frozen=True)
class RiskConfig:
    """Limits on paper and live trading (kestrel.execution.safety). Backtests ignore them."""

    max_order_usdt: Decimal
    """No single order above this notional."""
    max_daily_loss: Decimal
    """Halt when equity falls more than this fraction between two consecutive marks."""
    max_drawdown: Decimal
    """Halt when equity falls more than this fraction below its peak."""


@dataclass(frozen=True)
class InstrumentRules:
    """Exchange order constraints for one symbol."""

    symbol: str
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal

    def round_quantity(self, quantity: Decimal) -> Decimal:
        """Round down to the exchange's step size. Never up: that could exceed cash."""
        steps = (quantity / self.step_size).to_integral_value(rounding=ROUND_FLOOR)
        return steps * self.step_size


@dataclass(frozen=True)
class UniverseConfig:
    """Assumptions for coins that have no [instruments] entry of their own: the many coins
    a cross-sectional strategy ranks."""

    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    volume_window_days: int
    """Days whose median quote volume sets a coin's spread band."""
    spread_tiers: tuple[tuple[Decimal, Decimal], ...]
    """(minimum median daily quote volume, half-spread in bps), highest volume first,
    ending at a minimum of 0 (see kestrel.backtest.spread)."""


@dataclass(frozen=True)
class FxConfig:
    """Forex (P6): its own market, holdout and fixed pairs (docs/rules.md, 2026-11-23)."""

    holdout_start: datetime
    """First instant of forex's holdout (UTC); pre-registered before any forex data."""
    pairs: tuple[str, ...]
    """The fixed universe, as six-letter pairs (EURUSD)."""
    account_currency: str = "AUD"
    max_gross_leverage: Decimal = Decimal(3)
    """Largest sum of |weights| a strategy may ask for."""
    financing_markup_pct: Decimal = Decimal("2.5")
    """Broker markup on overnight financing, annual %, charged on both sides."""


@dataclass(frozen=True)
class Config:
    account_currency: str
    costs: CostConfig
    backtest: BacktestConfig
    instruments: dict[str, InstrumentRules]
    universe: UniverseConfig
    risk: RiskConfig
    document: dict[str, Any]
    fx: FxConfig | None = None
    """``[fx]``, when the config has one."""
    split: Split | None = None
    """The exploration experiment's split (kestrel.exploration), read from beside the
    config file; not part of the fingerprint, which covers the TOML document only."""
    confirmation_unlocked: bool = False
    """Set only for a frozen confirmation note's run: lets the engine use the confirmation
    half. Never read from a file."""

    def unlock_confirmation(self) -> Config:
        """This config, allowed to touch the confirmation half (one confirmation run)."""
        return replace(self, confirmation_unlocked=True)

    def rules_for(self, symbol: str) -> InstrumentRules:
        """A coin's own exchange rules if configured, else the universe's."""
        symbol = symbol.upper()
        if symbol in self.instruments:
            return self.instruments[symbol]
        u = self.universe
        return InstrumentRules(symbol, u.step_size, u.min_qty, u.min_notional)

    def instrument(self, symbol: str) -> InstrumentRules:
        try:
            return self.instruments[symbol.upper()]
        except KeyError:
            known = ", ".join(sorted(self.instruments)) or "none"
            raise ConfigError(
                f"no [instruments.{symbol.upper()}] in config (configured: {known})"
            ) from None

    def fingerprint(self) -> str:
        """Short, stable hash of the full config. Equal fingerprints, equal assumptions."""
        canonical = json.dumps(self.document, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def load_config(path: Path | str = "kestrel.toml") -> Config:
    path = Path(path)
    try:
        document = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None
    config = parse_config(document)
    try:
        split = load_split(path.parent / SPLIT_FILE)
    except ExplorationError as e:
        raise ConfigError(str(e)) from None
    return replace(config, split=split)


def parse_config(document: dict[str, Any]) -> Config:
    _expect_keys(
        {k: v for k, v in document.items() if k != "fx"},
        {"account_currency", "costs", "backtest", "instruments", "universe", "risk"},
        "top level",
    )

    currency = document["account_currency"]
    if not isinstance(currency, str) or not currency.isalpha():
        raise ConfigError(f"account_currency must be a currency code, got {currency!r}")
    currency = currency.upper()

    raw_costs = _table(document, "costs")
    _expect_keys(
        raw_costs,
        {"taker_fee_bps", "half_spread_bps", "impact_coef_bps", "max_participation"},
        "[costs]",
    )
    costs = CostConfig(
        taker_fee_bps=_decimal(raw_costs, "taker_fee_bps", "[costs]", low=0, high=100),
        half_spread_bps=_decimal(raw_costs, "half_spread_bps", "[costs]", low=0, high=100),
        impact_coef_bps=_decimal(raw_costs, "impact_coef_bps", "[costs]", low=0, high=10_000),
        max_participation=_decimal(
            raw_costs, "max_participation", "[costs]", low=0, high=1, low_inclusive=False
        ),
    )

    raw_bt = _table(document, "backtest")
    _expect_keys(raw_bt, {"no_trade_band", "risk_per_trade", "holdout_start"}, "[backtest]")
    holdout = raw_bt["holdout_start"]
    if isinstance(holdout, datetime) or not isinstance(holdout, date):
        raise ConfigError(
            f"[backtest] holdout_start must be a TOML date like 2025-11-01, got {holdout!r}"
        )
    backtest = BacktestConfig(
        no_trade_band=_decimal(raw_bt, "no_trade_band", "[backtest]", low=0, high=0.5),
        risk_per_trade=_decimal(
            raw_bt, "risk_per_trade", "[backtest]", low=0, high=0.1, low_inclusive=False
        ),
        holdout_start=datetime(holdout.year, holdout.month, holdout.day, tzinfo=UTC),
    )

    instruments: dict[str, InstrumentRules] = {}
    for symbol, raw in _table(document, "instruments").items():
        where = f"[instruments.{symbol}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} must be a table")
        if not symbol.isupper() or not symbol.endswith(currency):
            raise ConfigError(
                f"{where}: symbol must be upper case and quoted in {currency} "
                "(the engine does no FX conversion)"
            )
        _expect_keys(raw, {"step_size", "min_qty", "min_notional"}, where)
        instruments[symbol] = InstrumentRules(
            symbol=symbol,
            step_size=_decimal(raw, "step_size", where, low=0, low_inclusive=False),
            min_qty=_decimal(raw, "min_qty", where, low=0),
            min_notional=_decimal(raw, "min_notional", where, low=0),
        )

    raw_u = _table(document, "universe")
    _expect_keys(
        raw_u,
        {"step_size", "min_qty", "min_notional", "volume_window_days", "spread_tiers"},
        "[universe]",
    )
    window = raw_u["volume_window_days"]
    if isinstance(window, bool) or not isinstance(window, int) or not 2 <= window <= 365:
        raise ConfigError(
            "[universe] volume_window_days must be a whole number of days from 2 to 365, "
            f"got {window!r}"
        )
    universe = UniverseConfig(
        step_size=_decimal(raw_u, "step_size", "[universe]", low=0, low_inclusive=False),
        min_qty=_decimal(raw_u, "min_qty", "[universe]", low=0),
        min_notional=_decimal(raw_u, "min_notional", "[universe]", low=0),
        volume_window_days=window,
        spread_tiers=_spread_tiers(raw_u["spread_tiers"]),
    )

    raw_risk = _table(document, "risk")
    _expect_keys(raw_risk, {"max_order_usdt", "max_daily_loss", "max_drawdown"}, "[risk]")
    risk = RiskConfig(
        max_order_usdt=_decimal(raw_risk, "max_order_usdt", "[risk]", low=0, low_inclusive=False),
        max_daily_loss=_decimal(
            raw_risk, "max_daily_loss", "[risk]", low=0, high=1, low_inclusive=False
        ),
        max_drawdown=_decimal(
            raw_risk, "max_drawdown", "[risk]", low=0, high=1, low_inclusive=False
        ),
    )

    return Config(
        account_currency=currency,
        costs=costs,
        backtest=backtest,
        instruments=instruments,
        universe=universe,
        risk=risk,
        document=document,
        fx=_fx(document["fx"]) if "fx" in document else None,
    )


def _fx(raw: Any) -> FxConfig:
    if not isinstance(raw, dict):
        raise ConfigError("[fx] must be a table")
    _expect_keys(
        raw,
        {
            "holdout_start",
            "pairs",
            "account_currency",
            "max_gross_leverage",
            "financing_markup_pct",
        },
        "[fx]",
    )
    holdout = raw["holdout_start"]
    if isinstance(holdout, datetime) or not isinstance(holdout, date):
        raise ConfigError(
            f"[fx] holdout_start must be a TOML date like 2023-01-01, got {holdout!r}"
        )
    pairs = raw["pairs"]
    if (
        not isinstance(pairs, list)
        or not pairs
        or not all(
            isinstance(x, str) and len(x) == 6 and x.isalpha() and x.isupper() for x in pairs
        )
        or len(set(pairs)) != len(pairs)
    ):
        raise ConfigError(
            f"[fx] pairs must be distinct six-letter pairs like EURUSD, got {pairs!r}"
        )
    currency = raw["account_currency"]
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        raise ConfigError(f"[fx] account_currency must be a currency code, got {currency!r}")
    return FxConfig(
        datetime(holdout.year, holdout.month, holdout.day, tzinfo=UTC),
        tuple(pairs),
        currency.upper(),
        _decimal(raw, "max_gross_leverage", "[fx]", low=0, high=30, low_inclusive=False),
        _decimal(raw, "financing_markup_pct", "[fx]", low=0, high=20),
    )


def _spread_tiers(raw: Any) -> tuple[tuple[Decimal, Decimal], ...]:
    where = "[universe] spread_tiers"
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{where} must be a list of [min_volume_usdt, half_spread_bps] pairs")
    tiers = []
    for pair in raw:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ConfigError(
                f"{where}: each tier is [min_volume_usdt, half_spread_bps], got {pair!r}"
            )
        row = {"volume": pair[0], "bps": pair[1]}
        tiers.append(
            (_decimal(row, "volume", where, low=0), _decimal(row, "bps", where, low=0, high=1000))
        )
    volumes = [v for v, _ in tiers]
    if volumes != sorted(volumes, reverse=True) or len(set(volumes)) != len(volumes):
        raise ConfigError(f"{where}: list tiers from the highest volume down, each once")
    if volumes[-1] != 0:
        raise ConfigError(f"{where}: the last tier must start at 0 so every coin has a spread")
    return tuple(tiers)


def _table(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document[key]
    if not isinstance(value, dict):
        raise ConfigError(f"[{key}] must be a table")
    return value


def _expect_keys(table: dict[str, Any], expected: set[str], where: str) -> None:
    missing = expected - table.keys()
    unknown = table.keys() - expected
    if missing:
        raise ConfigError(f"{where}: missing {', '.join(sorted(missing))}")
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")


def _decimal(
    table: dict[str, Any],
    key: str,
    where: str,
    *,
    low: float,
    high: float | None = None,
    low_inclusive: bool = True,
) -> Decimal:
    raw = table[key]
    # bool is an int subclass; `true` is never a valid amount.
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise ConfigError(f"{where} {key} must be a number, got {raw!r}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise ConfigError(f"{where} {key} must be a number, got {raw!r}") from None
    if not value.is_finite():
        raise ConfigError(f"{where} {key} must be finite, got {raw!r}")
    too_low = value < Decimal(str(low)) if low_inclusive else value <= Decimal(str(low))
    too_high = high is not None and value > Decimal(str(high))
    if too_low or too_high:
        bound = f"{'>=' if low_inclusive else '>'} {low}" + (f" and <= {high}" if high else "")
        raise ConfigError(f"{where} {key} must be {bound}, got {raw!r}")
    return value
