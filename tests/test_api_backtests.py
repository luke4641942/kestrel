from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from tests.backtest.helpers import bars

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"
BODY = {"symbol": "BTCUSDT", "interval": "4h", "start": "2024-01", "end": "2024-01"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.H4, bars([100.0 + (i % 9) * 2 for i in range(60)]))
    store.write("BTCUSDT", Interval.H4, bars([100.0] * 30, start=datetime(2025, 11, 1, tzinfo=UTC)))
    return TestClient(create_app(tmp_path, static_dir=None, config_path=REPO_CONFIG))


def _finish(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/jobs/backtest", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError("backtest job did not finish")


def test_strategies_are_listed(client: TestClient) -> None:
    assert [s["key"] for s in client.get("/api/strategies").json()] == ["buy-and-hold"]


def test_backtest_job_saves_a_run_that_can_be_read_back(client: TestClient) -> None:
    job = _finish(client, BODY)
    assert job["status"] == "succeeded", job
    run_id = job["result"]["run_id"]

    (summary,) = client.get("/api/backtests").json()
    assert summary["id"] == run_id
    assert summary["initial_cash"] == "330.0"
    assert summary["metrics"]["trades"] == 1
    assert summary["benchmark_metrics"]["trades"] == 1
    assert "config" not in summary

    detail = client.get(f"/api/backtests/{run_id}").json()
    n = len(detail["equity"]["time"])
    assert n == 60 == len(detail["equity"]["equity"]) == len(detail["benchmark"]["drawdown"])
    assert max(detail["equity"]["drawdown"]) == 0
    assert min(detail["equity"]["drawdown"]) < 0
    assert detail["fills"][0]["side"] == "buy"
    assert detail["config"]["costs"]["taker_fee_bps"] == 10
    assert detail["code"]["commit"]


def test_api_cannot_reach_the_holdout(client: TestClient) -> None:
    job = _finish(client, {**BODY, "start": "2025-11", "end": "2025-11"})
    assert job["status"] == "failed"
    assert "holdout period" in job["error"]


def test_no_data_fails_the_job_with_a_reason(client: TestClient) -> None:
    job = _finish(client, {**BODY, "symbol": "ETHUSDT"})
    assert job["status"] == "failed"
    assert "ingest them first" in job["error"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"strategy": "magic"}, "unknown strategy"),
        ({"start": "2024-03", "end": "2024-01"}, "start is after end"),
        ({"initial_cash": 0}, "greater than 0"),
        ({"symbol": "BTC/USDT"}, "pair code"),
        ({"end": "2024-13"}, "YYYY-MM"),
    ],
)
def test_bad_requests_are_rejected(
    client: TestClient, change: dict[str, Any], message: str
) -> None:
    response = client.post("/api/jobs/backtest", json={**BODY, **change})
    assert response.status_code == 422
    assert message in response.text


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/backtests/20260101-000000-abcdef").status_code == 404
