from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.research import SECTIONS
from kestrel.strategy import STRATEGIES, BuyAndHold, StrategySpec
from tests.backtest.helpers import bars

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"
FILLED = {k: f"{k} text" for k in SECTIONS}
RUN = {"symbol": "BTCUSDT", "interval": "4h", "start": "2024-01", "end": "2024-01"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    ParquetStore(tmp_path / "data").write(
        "BTCUSDT", Interval.H4, bars([100.0 + (i % 9) * 2 for i in range(120)])
    )
    app = create_app(
        tmp_path / "data",
        static_dir=None,
        config_path=REPO_CONFIG,
        research_dir=tmp_path / "research",
    )
    return TestClient(app)


def _create(client: TestClient, name: str = "BTC trend") -> str:
    response = client.post("/api/research", json={"name": name})
    assert response.status_code == 201
    slug: str = response.json()["slug"]
    return slug


def _freeze(client: TestClient, slug: str, implementation: str = "ma-trend") -> None:
    edit = client.put(
        f"/api/research/{slug}/notes/1", json={"sections": FILLED, "implementation": implementation}
    )
    assert edit.status_code == 200, edit.text
    assert client.post(f"/api/research/{slug}/notes/1/freeze").status_code == 200


def _run(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/jobs/backtest", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_create_and_read(client: TestClient) -> None:
    slug = _create(client, "BTC Trend")
    detail = client.get(f"/api/research/{slug}").json()
    assert detail["status"] == "idea"
    assert detail["version"] == 1
    assert detail["frozen"] is False
    assert list(detail["sections"]) == list(SECTIONS)
    assert detail["trial_log"] == []
    assert detail["assistant_available"] is False
    assert [s["slug"] for s in client.get("/api/research").json()] == [slug]


def test_a_draft_cannot_be_backtested(client: TestClient) -> None:
    slug = _create(client)
    response = client.post("/api/jobs/backtest", json={**RUN, "research": slug})
    assert response.status_code == 422
    assert "before the results are seen" in response.text


def test_frozen_note_runs_as_a_logged_trial(client: TestClient) -> None:
    slug = _create(client)
    _freeze(client, slug)
    # The request names buy-and-hold, but the note pins ma-trend: the note wins.
    job = _run(client, {**RUN, "research": slug, "strategy": "buy-and-hold", "params": {"fast": 5}})
    assert job["status"] == "succeeded", job
    assert job["params"]["strategy"] == "ma-trend"

    detail = client.get(f"/api/research/{slug}").json()
    assert detail["status"] == "backtested"
    (trial,) = detail["trial_log"]
    assert trial["research"] == {"slug": slug, "version": 1}
    assert trial["params"] == {"fast": 5, "slow": 100}
    assert detail["runs"] == detail["trials"] == 1


def test_identical_reruns_do_not_inflate_the_trial_count(client: TestClient) -> None:
    slug = _create(client)
    _freeze(client, slug)
    body = {**RUN, "research": slug, "params": {"fast": 5}}
    _run(client, body)
    _run(client, body)
    _run(client, {**body, "params": {"fast": 6}})
    summary = client.get("/api/research").json()[0]
    assert summary["runs"] == 3
    assert summary["trials"] == 2


def test_edits_after_freezing_are_refused(client: TestClient) -> None:
    slug = _create(client)
    _freeze(client, slug)
    response = client.put(f"/api/research/{slug}/notes/1", json={"sections": {"hypothesis": "new"}})
    assert response.status_code == 409
    assert "frozen" in response.text


def test_new_version_and_reject(client: TestClient) -> None:
    slug = _create(client)
    _freeze(client, slug)
    v2 = client.post(f"/api/research/{slug}/notes", json={"reason": "Lower turnover"}).json()
    assert v2["version"] == 2
    assert v2["frozen"] is False
    assert v2["notes"][1]["change_reason"] == "Lower turnover"
    # v2 is a draft, so it can't be tested until it's frozen too.
    assert client.post("/api/jobs/backtest", json={**RUN, "research": slug}).status_code == 422

    rejected = client.post(f"/api/research/{slug}/reject", json={"reason": "No edge after costs"})
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["history"][-1]["reason"] == "No edge after costs"


def test_freezing_an_incomplete_note_is_a_conflict(client: TestClient) -> None:
    slug = _create(client)
    response = client.post(f"/api/research/{slug}/notes/1/freeze")
    assert response.status_code == 409
    assert "fill in every section" in response.text


def test_unknown_research_strategy(client: TestClient) -> None:
    assert client.get("/api/research/nope").status_code == 404
    assert client.post("/api/research/nope/reject", json={"reason": "x"}).status_code == 404


def test_research_code_cannot_bypass_its_note(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = StrategySpec("secret", "research", "A research implementation.", BuyAndHold)
    monkeypatch.setitem(STRATEGIES, "secret", spec)
    response = client.post("/api/jobs/backtest", json={**RUN, "strategy": "secret"})
    assert response.status_code == 422
    assert "through their research note" in response.text


def test_engine_tests_run_directly_with_params(client: TestClient) -> None:
    job = _run(client, {**RUN, "strategy": "ma-trend", "params": {"fast": 3, "slow": 12}})
    assert job["status"] == "succeeded", job
    (run,) = client.get("/api/backtests").json()
    assert run["research"] is None
    assert run["params"] == {"fast": 3, "slow": 12}


def test_bad_params_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/jobs/backtest", json={**RUN, "strategy": "ma-trend", "params": {"fast": 0}}
    )
    assert response.status_code == 422
    assert "must be 2 to 200" in response.text


def test_source_endpoint(client: TestClient) -> None:
    body = client.get("/api/strategies/ma-trend/source").json()
    assert body["source"].startswith("class MovingAverageTrend")
    assert body["commit"]
    assert client.get("/api/strategies/magic/source").status_code == 404


def test_signals_endpoint(client: TestClient) -> None:
    response = client.post(
        "/api/strategies/ma-trend/signals", json={**RUN, "params": {"fast": 3, "slow": 12}}
    )
    body = response.json()
    assert response.status_code == 200, response.text
    assert len(body["time"]) == len(body["weight"]) == len(body["close"]) == 120
    assert set(body["weight"]) <= {0.0, 1.0}
    assert body["weight"][:11] == [0.0] * 11  # slow window not full
    assert body["total_bars"] == 120


def test_signals_are_computed_on_the_whole_range_and_only_drawing_is_capped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: the bars were cut to the cap before computing, so signals near the start
    # of what was drawn lacked their warm-up and differed from a backtest's.
    import kestrel.api.app as app_module

    params = {"fast": 3, "slow": 12}
    full = client.post("/api/strategies/ma-trend/signals", json={**RUN, "params": params}).json()
    monkeypatch.setattr(app_module, "MAX_BARS", 50)
    capped = client.post("/api/strategies/ma-trend/signals", json={**RUN, "params": params}).json()
    assert capped["total_bars"] == 120
    assert capped["time"] == full["time"][-50:]
    assert capped["weight"] == full["weight"][-50:]


@pytest.mark.parametrize(
    ("key", "body", "code"),
    [
        ("ma-trend", {**RUN, "params": {"fast": 0}}, 422),
        ("magic", RUN, 422),
        ("ma-trend", {**RUN, "symbol": "ETHUSDT"}, 404),
    ],
)
def test_signals_errors(client: TestClient, key: str, body: dict[str, Any], code: int) -> None:
    assert client.post(f"/api/strategies/{key}/signals", json=body).status_code == code


def test_signals_refuse_a_strategy_that_looks_ahead(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.backtest.test_engine import PeeksAhead

    spec = StrategySpec("peek", "engine-test", "Cheats.", lambda symbol: PeeksAhead())
    monkeypatch.setitem(STRATEGIES, "peek", spec)
    response = client.post("/api/strategies/peek/signals", json=RUN)
    assert response.status_code == 422
    assert "information from the future" in response.text
