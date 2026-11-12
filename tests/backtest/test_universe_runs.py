"""A cross-sectional run end to end: stored coins and manifest, the portfolio engine, the
BTC benchmark, a saved run, and the API that serves it."""

from __future__ import annotations

import json
import math
import random
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.backtest.runs import RunRequest, RunStore, execute
from kestrel.config import load_config
from kestrel.data.binance_vision import Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.data.universe import manifest_path
from tests.backtest.helpers import bars

REPO_CONFIG = Path(__file__).resolve().parents[2] / "kestrel.toml"
D1 = Interval.D1
START = datetime(2023, 1, 1, tzinfo=UTC)
PARAMS = {"min_history_days": 30}


def _walk(seed: int, n: int = 365) -> list[float]:
    rnd = random.Random(seed)
    price, out = 100.0, []
    for _ in range(n):
        price *= math.exp(rnd.gauss(0.001, 0.04))
        out.append(price)
    return out


def _store_universe(root: Path, coins: int = 8) -> ParquetStore:
    store = ParquetStore(root)
    names = [f"C{k}USDT" for k in range(coins)]
    for k, name in enumerate(["BTCUSDT", *names]):
        closes = _walk(k)
        store.write(
            name,
            D1,
            bars([closes[0], *closes[:-1]], closes=closes, start=START, step=timedelta(days=1)),
        )
    path = manifest_path(store, D1)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"coins": {n: 12 for n in ["BTCUSDT", *names]}}))
    return store


def _request(**over: Any) -> RunRequest:
    fields: dict[str, Any] = dict(
        strategy="xs-momentum",
        symbol="UNIVERSE",
        interval=D1,
        start=Month.parse("2023-01"),
        end=Month.parse("2023-12"),
        initial_cash=Decimal(1000),
        params=PARAMS,
    )
    return RunRequest(**{**fields, **over})


def test_a_universe_run_is_benchmarked_on_btc_and_saved(tmp_path: Path) -> None:
    store = _store_universe(tmp_path)
    cfg = load_config(REPO_CONFIG)
    outcome = execute(_request(), store, cfg)
    assert outcome.result.symbol == "UNIVERSE"
    assert outcome.benchmark.symbol == "BTCUSDT"
    assert outcome.universe_coins == 9
    assert outcome.metrics.trades > 0
    run_id = RunStore(tmp_path).save(outcome, cfg)
    meta = RunStore(tmp_path).load(run_id).meta
    assert (meta["benchmark_symbol"], meta["universe_coins"], meta["symbol"]) == (
        "BTCUSDT",
        9,
        "UNIVERSE",
    )
    assert meta["forced_exits"] == 0


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"symbol": "BTCUSDT", "strategy": "xs-momentum"}, "runs on UNIVERSE, not BTCUSDT"),
        ({"strategy": "buy-and-hold"}, "trades one market"),
        ({"engine": "vectorised"}, "event engine only"),
        ({"start": Month.parse("2021-01"), "end": Month.parse("2021-02")}, "no universe bars"),
    ],
)
def test_universe_requests_that_cannot_run(
    tmp_path: Path, over: dict[str, Any], message: str
) -> None:
    store = _store_universe(tmp_path)
    with pytest.raises(ValueError, match=message):
        execute(_request(**over), store, load_config(REPO_CONFIG))


def test_a_missing_universe_says_how_to_get_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="kestrel ingest-universe --interval 1d"):
        execute(_request(), ParquetStore(tmp_path), load_config(REPO_CONFIG))


def test_the_benchmark_needs_btc(tmp_path: Path) -> None:
    store = _store_universe(tmp_path)
    for p in (tmp_path / "klines" / "venue=binance" / "symbol=BTCUSDT").rglob("*.parquet"):
        p.unlink()
    with pytest.raises(ValueError, match="benchmark needs BTCUSDT"):
        execute(_request(), store, load_config(REPO_CONFIG))


def test_the_api_runs_and_serves_a_universe_run(tmp_path: Path) -> None:
    _store_universe(tmp_path)
    client = TestClient(
        create_app(
            tmp_path, static_dir=None, config_path=REPO_CONFIG, research_dir=tmp_path / "research"
        )
    )
    listed = {s["key"]: s for s in client.get("/api/strategies").json()}
    assert listed["xs-momentum"]["universe"] is True
    assert listed["buy-and-hold"]["universe"] is False
    signals = client.post(
        "/api/strategies/xs-momentum/signals",
        json={"symbol": "BTCUSDT", "interval": "1d", "start": "2023-01", "end": "2023-03"},
    )
    assert signals.status_code == 422  # no single-market signals for a universe strategy

    body = {
        "strategy": "xs-momentum",
        "symbol": "UNIVERSE",
        "interval": "1d",
        "start": "2023-01",
        "end": "2023-12",
        "params": PARAMS,
    }
    # Research strategies only run through a frozen note; run it as a trial of one.
    note = client.post("/api/research", json={"name": "Momentum test"}).json()
    sections = {k: "x" for k in note["sections"]}
    client.put(
        f"/api/research/{note['slug']}/notes/1",
        json={"sections": sections, "implementation": "xs-momentum"},
    )
    client.post(f"/api/research/{note['slug']}/notes/1/freeze")
    job = client.post("/api/jobs/backtest", json={**body, "research": note["slug"]}).json()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["finished_at"]:
            break
        time.sleep(0.05)
    assert job["status"] == "succeeded", job
    (run,) = client.get("/api/backtests").json()
    assert (run["symbol"], run["benchmark_symbol"], run["universe_coins"]) == (
        "UNIVERSE",
        "BTCUSDT",
        9,
    )
    detail = client.get(f"/api/backtests/{run['id']}").json()
    assert {f["side"] for f in detail["fills"]} <= {"buy", "sell"}


def test_a_failed_trial_is_not_logged_and_does_not_say_it_was(tmp_path: Path) -> None:
    # Regression: the job log said "Logged as a trial" before running; when the universe
    # was missing the run failed and nothing was logged, but the log claimed otherwise.
    client = TestClient(
        create_app(tmp_path, static_dir=None, config_path=REPO_CONFIG, research_dir=tmp_path / "r")
    )
    note = client.post("/api/research", json={"name": "Momentum"}).json()
    sections = {k: "x" for k in note["sections"]}
    client.put(
        f"/api/research/{note['slug']}/notes/1",
        json={"sections": sections, "implementation": "xs-momentum"},
    )
    client.post(f"/api/research/{note['slug']}/notes/1/freeze")
    body = {
        "strategy": "xs-momentum",
        "symbol": "UNIVERSE",
        "interval": "1d",
        "start": "2023-01",
        "end": "2023-12",
        "research": note["slug"],
    }
    job = client.post("/api/jobs/backtest", json=body).json()
    deadline = time.monotonic() + 10
    while not job["finished_at"] and time.monotonic() < deadline:
        time.sleep(0.05)
        job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "failed"
    assert "ingest-universe" in job["error"]
    assert not any(line.split(" ", 1)[1].startswith("Logged as a trial") for line in job["logs"])
    assert client.get(f"/api/research/{note['slug']}").json()["trials"] == 0


def test_the_report_names_btc_as_the_benchmark(tmp_path: Path) -> None:
    # Regression: the text report said "vs UNIVERSE" for a cross-sectional run.
    from kestrel.backtest.metrics import format_report

    outcome = execute(_request(), _store_universe(tmp_path), load_config(REPO_CONFIG))
    report = format_report(
        outcome.result, outcome.metrics, outcome.analysis, outcome.benchmark.asset
    )
    assert "vs BTC" in report
    assert "vs UNIVERSE" not in report
