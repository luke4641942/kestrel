"""The regimes API: fitting as a job, never on the holdout, and reading the stored fit."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kestrel import regimes as rg
from kestrel.api import app as app_module
from kestrel.api.app import create_app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from tests.backtest.helpers import bars
from tests.test_regimes import FAST, regime_bars

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(app_module, "walk_regimes", partial(rg.walk_forward, preset=FAST))
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.D1, regime_bars(560))  # 2024-01-01 to mid-2025
    # Holdout days, which a fit must never read.
    holdout = datetime(2025, 11, 1, tzinfo=UTC)
    store.write("BTCUSDT", Interval.D1, bars([100.0] * 20, start=holdout, step=timedelta(days=1)))
    store.write("ETHUSDT", Interval.D1, regime_bars(100))
    return TestClient(create_app(tmp_path, static_dir=None, config_path=REPO_CONFIG))


def _finish(client: TestClient, symbol: str) -> dict[str, Any]:
    response = client.post("/api/jobs/regimes", json={"symbol": symbol})
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError("regimes job did not finish")


def test_a_fit_is_saved_and_read_back(client: TestClient) -> None:
    assert client.get("/api/regimes").json() == []
    job = _finish(client, "btcusdt")
    assert job["status"] == "succeeded", job
    assert job["result"] == {"symbol": "BTCUSDT"}
    (summary,) = client.get("/api/regimes").json()
    assert summary["symbol"] == "BTCUSDT"
    assert summary["refits"] > 0
    d = client.get("/api/regimes/BTCUSDT").json()
    n = len(d["time"])
    assert n == len(d["close"]) == len(d["probabilities"]) == len(d["state"])
    assert n == 560  # the holdout days were never read
    assert datetime.fromisoformat(d["last"]) < datetime(2025, 11, 1, tzinfo=UTC)
    assert d["probabilities"][0] is None  # before the first fit
    assert len(d["states"]) == FAST.states
    assert len(d["transitions"]) == FAST.states
    assert d["preset"]["train_days"] == FAST.train_days
    first = next(i for i, p in enumerate(d["probabilities"]) if p is not None)
    assert d["state"][first] == max(range(FAST.states), key=lambda k: d["probabilities"][first][k])


def test_missing_markets_and_short_histories(client: TestClient) -> None:
    assert client.get("/api/regimes/SOLUSDT").status_code == 404
    none = _finish(client, "SOLUSDT")
    assert none["status"] == "failed"
    assert "ingest SOLUSDT 1d first" in none["error"]
    short = _finish(client, "ETHUSDT")
    assert short["status"] == "failed"
    assert "too little daily history" in short["error"]
    assert client.post("/api/jobs/regimes", json={"symbol": "x"}).status_code == 422
