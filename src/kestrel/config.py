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
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Any


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
    holdout_start: datetime
    """First instant of the holdout period (UTC). Research must stay before it."""


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
class Config:
    account_currency: str
    costs: CostConfig
    backtest: BacktestConfig
    instruments: dict[str, InstrumentRules]
    document: dict[str, Any]

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
    return parse_config(document)


def parse_config(document: dict[str, Any]) -> Config:
    _expect_keys(document, {"account_currency", "costs", "backtest", "instruments"}, "top level")

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
    _expect_keys(raw_bt, {"no_trade_band", "holdout_start"}, "[backtest]")
    holdout = raw_bt["holdout_start"]
    if isinstance(holdout, datetime) or not isinstance(holdout, date):
        raise ConfigError(
            f"[backtest] holdout_start must be a TOML date like 2025-11-01, got {holdout!r}"
        )
    backtest = BacktestConfig(
        no_trade_band=_decimal(raw_bt, "no_trade_band", "[backtest]", low=0, high=0.5),
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

    return Config(
        account_currency=currency,
        costs=costs,
        backtest=backtest,
        instruments=instruments,
        document=document,
    )


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
