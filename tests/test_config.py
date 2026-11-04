from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from kestrel.config import ConfigError, InstrumentRules, load_config, parse_config

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"


@pytest.fixture
def document() -> dict[str, Any]:
    return {
        "account_currency": "USDT",
        "costs": {
            "taker_fee_bps": 10,
            "half_spread_bps": 1,
            "impact_coef_bps": 100,
            "max_participation": 0.01,
        },
        "backtest": {"no_trade_band": 0.02, "holdout_start": date(2025, 11, 1)},
        "instruments": {
            "BTCUSDT": {"step_size": "0.00001", "min_qty": "0.00001", "min_notional": "5"},
        },
    }


def test_repo_config_loads() -> None:
    config = load_config(REPO_CONFIG)
    assert config.account_currency == "USDT"
    assert config.costs.taker_fee_bps == Decimal(10)
    assert config.instrument("btcusdt").step_size == Decimal("0.00001")
    assert config.backtest.no_trade_band == Decimal("0.02")
    assert config.backtest.holdout_start == datetime(2025, 11, 1, tzinfo=UTC)


def test_numbers_are_exact_decimals(document: dict[str, Any]) -> None:
    # 0.01 as a float is 0.01000000000000000020816...; the config must hold exactly 0.01.
    assert parse_config(document).costs.max_participation == Decimal("0.01")


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("account_currency = ")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["costs"].pop("taker_fee_bps"), r"\[costs\]: missing taker_fee_bps"),
        (lambda d: d["costs"].update(taker_fees_bps=10), r"unknown key\(s\) taker_fees_bps"),
        (lambda d: d["costs"].update(taker_fee_bps=-1), "taker_fee_bps must be >= 0"),
        (lambda d: d["costs"].update(taker_fee_bps=True), "must be a number"),
        (lambda d: d["costs"].update(max_participation=0), "max_participation must be > 0"),
        (lambda d: d["costs"].update(max_participation=1.5), "<= 1"),
        (lambda d: d["costs"].update(half_spread_bps="nan"), "must be finite"),
        (lambda d: d["instruments"]["BTCUSDT"].update(step_size="0"), "step_size must be > 0"),
        (lambda d: d["instruments"].update(ETHBTC=d["instruments"]["BTCUSDT"]), "quoted in USDT"),
        (lambda d: d.update(account_currency="US$"), "currency code"),
        (lambda d: d.pop("backtest"), "top level: missing backtest"),
        (lambda d: d.update(costs=10), r"\[costs\] must be a table"),
        (lambda d: d["instruments"].update(BTCUSDT="x"), "must be a table"),
        (lambda d: d["costs"].update(half_spread_bps="one"), "must be a number, got 'one'"),
        (
            lambda d: d["backtest"].update(no_trade_band=0.6),
            "no_trade_band must be >= 0 and <= 0.5",
        ),
        (lambda d: d["backtest"].update(holdout_start="2025-11-01"), "must be a TOML date"),
        (
            lambda d: d["backtest"].update(holdout_start=datetime(2025, 11, 1, tzinfo=UTC)),
            "must be a TOML date",
        ),
    ],
)
def test_invalid_values_are_rejected_with_a_clear_message(
    document: dict[str, Any], mutate: Any, message: str
) -> None:
    mutate(document)
    with pytest.raises(ConfigError, match=message):
        parse_config(document)


def test_unknown_instrument(document: dict[str, Any]) -> None:
    with pytest.raises(ConfigError, match=r"no \[instruments.SOLUSDT\].*configured: BTCUSDT"):
        parse_config(document).instrument("SOLUSDT")


def test_fingerprint_is_stable_and_tracks_changes(document: dict[str, Any]) -> None:
    same = copy.deepcopy(document)
    changed = copy.deepcopy(document)
    changed["costs"]["taker_fee_bps"] = 7.5
    fp = parse_config(document).fingerprint()
    assert fp == parse_config(same).fingerprint()
    assert fp != parse_config(changed).fingerprint()
    assert len(fp) == 12


@pytest.mark.parametrize(
    ("qty", "expected"),
    [("0.123456789", "0.12345"), ("0.00001", "0.00001"), ("0.000019", "0.00001"), ("0", "0")],
)
def test_round_quantity_floors_to_step(qty: str, expected: str) -> None:
    rules = InstrumentRules("BTCUSDT", Decimal("0.00001"), Decimal("0.00001"), Decimal(5))
    assert rules.round_quantity(Decimal(qty)) == Decimal(expected)
