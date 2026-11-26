from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
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
    listed = {s["key"]: s for s in client.get("/api/strategies").json()}
    assert listed["buy-and-hold"]["kind"] == "benchmark"
    assert listed["ma-trend"]["kind"] == "engine-test"
    assert [p["name"] for p in listed["ma-trend"]["params"]] == ["fast", "slow"]


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
    assert detail["config"]["costs"]["taker_fee_bps"] == 7.5
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


def test_a_lookahead_strategy_fails_the_job_with_a_clear_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kestrel.strategy import STRATEGIES, StrategySpec
    from tests.backtest.test_engine import PeeksAhead

    spec = StrategySpec("peek", "engine-test", "Cheats.", lambda symbol: PeeksAhead())
    monkeypatch.setitem(STRATEGIES, "peek", spec)
    job = _finish(client, {**BODY, "strategy": "peek"})
    assert job["status"] == "failed"
    assert "information from the future" in job["error"]
    assert not any("Traceback" in line for line in job["logs"])


def test_bracket_backtest_returns_trades_and_r_metrics(tmp_path: Path) -> None:
    import math

    closes = [100 + 10 * math.sin(i / 7) + i * 0.05 for i in range(400)]
    ParquetStore(tmp_path).write(
        "BTCUSDT",
        Interval.H4,
        bars(
            closes,
            closes=closes,
            highs=[c * 1.004 for c in closes],
            lows=[c * 0.996 for c in closes],
        ),
    )
    client = TestClient(create_app(tmp_path, static_dir=None, config_path=REPO_CONFIG))
    job = _finish(
        client,
        {**BODY, "end": "2024-03", "strategy": "channel-breakout", "params": {"target_r": 2}},
    )
    assert job["status"] == "succeeded", job
    detail = client.get(f"/api/backtests/{job['result']['run_id']}").json()
    assert detail["style"] == "brackets"
    assert detail["params"] == {"lookback": 20, "stop_lookback": 10, "target_r": 2}
    assert len(detail["trades"]) == detail["metrics"]["round_trips"] >= 3
    assert detail["metrics"]["avg_cost_r"] > 0
    assert detail["trades"][0]["exit_reason"] in {"target", "stop", "end of data"}
    assert detail["benchmark_metrics"]["round_trips"] is None

    signals = client.post(
        "/api/strategies/channel-breakout/signals", json={**BODY, "end": "2024-03"}
    ).json()
    assert signals["kind"] == "entries"
    assert set(signals["weight"]) == {0.0, 1.0}


def test_signals_never_reach_the_holdout(client: TestClient) -> None:
    for end in ("2025-11", "2026-03"):
        res = client.post("/api/strategies/ma-trend/signals", json={**BODY, "end": end})
        assert res.status_code == 422
        assert "reaches the holdout" in res.json()["detail"]
    ok = client.post("/api/strategies/ma-trend/signals", json={**BODY, "end": "2025-10"})
    assert ok.status_code == 200


def test_run_detail_includes_the_analysis(client: TestClient) -> None:
    job = _finish(client, BODY)
    detail = client.get(f"/api/backtests/{job['result']['run_id']}").json()
    a = detail["analysis"]
    assert a["resamples"] == 2000
    assert 0 <= a["p_profit"] <= 1
    assert a["beta"] == pytest.approx(1)  # buy-and-hold against itself
    assert set(a["holding_buckets"]) == {"under 1 month", "1 to 12 months", "over 12 months"}


def test_runs_saved_before_the_analysis_still_load(client: TestClient, tmp_path: Path) -> None:
    import json

    job = _finish(client, BODY)
    run_id = job["result"]["run_id"]
    path = tmp_path / "runs" / run_id / "run.json"
    meta = json.loads(path.read_text())
    del meta["analysis"]
    path.write_text(json.dumps(meta))
    assert client.get(f"/api/backtests/{run_id}").json()["analysis"] is None


def test_runs_saved_before_start_dates_get_them_from_their_equity(
    client: TestClient, tmp_path: Path
) -> None:
    import json

    job = _finish(client, BODY)
    run_id = job["result"]["run_id"]
    path = tmp_path / "runs" / run_id / "run.json"
    meta = json.loads(path.read_text())
    stored = meta["analysis"].pop("start_dates")
    path.write_text(json.dumps(meta))
    a = client.get(f"/api/backtests/{run_id}").json()["analysis"]
    assert a["start_dates"] == stored
    assert [r["window_days"] for r in stored] == [365, 730]


def test_runs_are_deflated_for_every_strategy_tried(client: TestClient) -> None:
    for fast in (3, 4):
        _finish(client, {**BODY, "strategy": "ma-trend", "params": {"fast": fast, "slow": 12}})
    _finish(client, BODY)  # buy-and-hold: the benchmark, not a trial
    validation = client.get("/api/validation").json()
    assert validation["trials"] == 2
    assert validation["expected_max_sharpe"] >= 0
    assert validation["pools"] == {"main": 2, "confirmation": 0, "forex": 0}
    assert validation["all_trials"] == 2
    listed = client.get("/api/backtests").json()
    by = {r["strategy"]: r for r in listed}
    assert by["buy-and-hold"]["deflated_sharpe"] is None
    tried = [r for r in listed if r["strategy"] == "ma-trend"]
    assert all(r["deflated_trials"] == 2 for r in tried)
    detail = client.get(f"/api/backtests/{tried[0]['id']}").json()
    assert detail["deflated_trials"] == 2
    history = client.get("/api/validation/history").json()
    assert [p["trials"] for p in history] == [1, 2]  # the benchmark run adds no point
    assert history[-1]["luck_bar"] == pytest.approx(validation["expected_max_sharpe"])


def test_signals_carry_indicators_over_the_same_bars(client: TestClient) -> None:
    body = {**BODY, "indicators": ["ema20"]}
    signals = client.post("/api/strategies/ma-trend/signals", json=body).json()
    ema = signals["indicators"]["ema20"]["value"]
    assert len(ema) == len(signals["time"])
    assert ema[18] is None  # EMA 20 is ready from the 20th bar
    assert ema[19] is not None


def test_undefined_indicator_values_are_sent_as_null(tmp_path: Path) -> None:
    # Zero volume everywhere: relative volume is 0 / 0, which must not reach JSON as NaN.
    ParquetStore(tmp_path).write(
        "BTCUSDT", Interval.H4, bars([100.0] * 60).with_columns(volume=pl.lit(0.0))
    )
    app = create_app(tmp_path, static_dir=None, config_path=REPO_CONFIG)
    body = {**BODY, "indicators": ["rvol20"]}
    signals = TestClient(app).post("/api/strategies/ma-trend/signals", json=body).json()
    assert signals["indicators"]["rvol20"]["value"] == [None] * 60


def test_run_detail_carries_its_scorecard(client: TestClient, tmp_path: Path) -> None:
    from kestrel.validation.log import Look, Study, ValidationLog

    held = _finish(client, BODY)
    assert client.get(f"/api/backtests/{held['result']['run_id']}").json()["scorecard"] is None
    params = {"fast": 3, "slow": 12}
    job = _finish(client, {**BODY, "strategy": "ma-trend", "params": params})
    run_id = job["result"]["run_id"]
    card = client.get(f"/api/backtests/{run_id}").json()["scorecard"]
    assert [g["name"] for g in card["grades"]][:2] == ["Edge", "Luck"]
    by = {g["name"]: g for g in card["grades"]}
    assert by["Out of sample"]["status"] == "not run"
    assert by["Enough trades"]["status"] == "fail"  # a month of 4h bars
    assert card["verdict"] == "fail"
    # A walk-forward on the same market and period is picked up.
    ValidationLog(tmp_path).save(
        Study("walk-forward", "ma-trend", "BTCUSDT", "4h", "2024-01", "2024-01",
              [Look(params, 0.5)], "SURVIVES", {"folds": 3, "windows_beating_holding": 2})
    )  # fmt: skip
    card = client.get(f"/api/backtests/{run_id}").json()["scorecard"]
    out = next(g for g in card["grades"] if g["name"] == "Out of sample")
    assert (out["status"], out["measured"]) == ("pass", "Beat holding in 2 of 3 test windows")


def test_runs_saved_before_concentration_get_it_from_their_equity(
    client: TestClient, tmp_path: Path
) -> None:
    import json

    job = _finish(client, BODY)
    run_id = job["result"]["run_id"]
    path = tmp_path / "runs" / run_id / "run.json"
    meta = json.loads(path.read_text())
    stored = meta["analysis"].pop("concentration")
    held = meta["analysis"].pop("hold_concentration")
    path.write_text(json.dumps(meta))
    a = client.get(f"/api/backtests/{run_id}").json()["analysis"]
    assert a["concentration"] == pytest.approx(stored)  # the same as when it was saved
    assert a["hold_concentration"] == pytest.approx(held)
