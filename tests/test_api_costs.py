from __future__ import annotations

import math
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.backtest.costs import CostModel, Fill, Side
from kestrel.config import load_config

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"
QUOTE = {"notional": 330, "price": 60_000, "bar_volume": 500_000_000}


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "kestrel.toml"
    shutil.copy(REPO_CONFIG, path)
    return path


@pytest.fixture
def client(tmp_path: Path, config_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path, static_dir=None, config_path=config_path))


def test_costs_returns_the_config(client: TestClient) -> None:
    body = client.get("/api/costs").json()
    assert body["account_currency"] == "USDT"
    assert body["costs"]["taker_fee_bps"] == 10
    assert body["backtest"] == {"no_trade_band": 0.02, "holdout_start": "2025-11-01T00:00:00Z"}
    assert body["fingerprint"] == load_config(REPO_CONFIG).fingerprint()
    assert {i["symbol"] for i in body["instruments"]} >= {"BTCUSDT", "ETHUSDT"}


@pytest.mark.parametrize("side", list(Side))
def test_quote_is_the_engine_fill(client: TestClient, side: Side) -> None:
    body = client.get("/api/costs/quote", params={**QUOTE, "side": side.value}).json()
    config = load_config(REPO_CONFIG)
    fill = CostModel(config.costs).fill_market(
        side,
        Decimal(330) / 60_000,
        Decimal(60_000),
        Decimal(500_000_000),
        config.instrument("BTCUSDT"),
    )
    assert isinstance(fill, Fill)
    assert body["filled"] is True
    assert body["quantity"] == float(fill.quantity) == 0.0055
    assert body["total_cost"] == pytest.approx(float(fill.total_cost), rel=1e-12)
    assert body["cash_flow"] == pytest.approx(float(fill.cash_flow), rel=1e-12)
    assert (body["cash_flow"] < 0) is (side is Side.BUY)


def test_quote_rejection_carries_the_reason(client: TestClient) -> None:
    body = client.get("/api/costs/quote", params={**QUOTE, "notional": 3}).json()
    assert body["filled"] is False
    assert "below the minimum 5" in body["reason"]
    assert body["total_cost"] is None


def test_unknown_symbol_is_404(client: TestClient) -> None:
    response = client.get("/api/costs/quote", params={**QUOTE, "symbol": "SOLUSDT"})
    assert response.status_code == 404
    assert "SOLUSDT" in response.json()["detail"]


def test_broken_config_is_a_clear_503(client: TestClient, config_path: Path) -> None:
    config_path.write_text(config_path.read_text().replace("taker_fee_bps", "taker_fees_bps"))
    response = client.get("/api/costs")
    assert response.status_code == 503
    assert "[costs]: missing taker_fee_bps" in response.json()["detail"]


def test_config_edits_apply_without_restart(client: TestClient, config_path: Path) -> None:
    config_path.write_text(
        config_path.read_text().replace("taker_fee_bps = 10", "taker_fee_bps = 7.5")
    )
    assert client.get("/api/costs").json()["costs"]["taker_fee_bps"] == 7.5


def test_curve_spans_the_fillable_range(client: TestClient) -> None:
    body = client.get(
        "/api/costs/curve", params={"price": 60_000, "bar_volume": 1_000_000, "points": 50}
    ).json()
    points = body["points"]
    assert body["min_notional"] == 5
    assert body["max_notional"] == 10_000  # 1% of 1m
    assert 45 <= len(points) <= 50
    assert all(5 <= p["notional"] <= 10_000 for p in points)
    totals = [p["total_bps"] for p in points]
    assert totals == sorted(totals)  # bigger orders never cost less per unit
    for p in points:
        assert p["spread_bps"] == pytest.approx(1)
        assert p["impact_bps"] == pytest.approx(100 * math.sqrt(p["notional"] / 1_000_000))
        assert p["total_bps"] == pytest.approx(p["fee_bps"] + p["spread_bps"] + p["impact_bps"])
    # At the 1% cap impact is 100 bps * sqrt(0.01) = 10 bps.
    assert points[-1]["impact_bps"] == pytest.approx(10, rel=1e-3)


def test_costs_include_the_universe_spread_bands(client: TestClient) -> None:
    tiers = client.get("/api/costs").json()["spread_tiers"]
    assert tiers[0] == [500_000_000, 1]
    assert tiers[-1][0] == 0
