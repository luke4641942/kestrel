from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.data.binance_vision import BinanceVisionClient, Month, archive_url, parse_archive
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from tests.conftest import ArchiveFactory, checksum_for


def _mock_binance(make_archive: ArchiveFactory, months: list[Month]) -> BinanceVisionClient:
    routes: dict[str, httpx.Response] = {}
    for m in months:
        payload = make_archive(start=datetime(m.year, m.month, 1, tzinfo=UTC), n=24)
        url = archive_url("BTCUSDT", Interval.H1, m)
        routes[url] = httpx.Response(200, content=payload)
        routes[url + ".CHECKSUM"] = httpx.Response(200, text=checksum_for(payload, "f.zip"))

    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return BinanceVisionClient(httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def seeded(tmp_path: Path, make_archive: ArchiveFactory) -> Path:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, parse_archive(make_archive(n=48)))
    return tmp_path


def _wait(client: TestClient, job_id: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_health(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, static_dir=None)) as client:
        body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_openapi_docs_are_published(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, static_dir=None)) as client:
        assert "/api/jobs/ingest" in client.get("/api/openapi.json").json()["paths"]


def test_datasets_lists_stats(seeded: Path) -> None:
    with TestClient(create_app(seeded, static_dir=None)) as client:
        (ds,) = client.get("/api/datasets").json()
    assert ds["symbol"] == "BTCUSDT"
    assert ds["interval"] == "1h"
    assert ds["rows"] == 48
    assert ds["months"] == 1


def test_bars_default_to_most_recent(seeded: Path) -> None:
    with TestClient(create_app(seeded, static_dir=None)) as client:
        bars = client.get("/api/datasets/BTCUSDT/1h/bars", params={"limit": 10}).json()
    assert len(bars["time"]) == 10
    assert bars["time"] == sorted(bars["time"])
    assert bars["time"][-1] == int(datetime(2024, 1, 2, 23, tzinfo=UTC).timestamp())


def test_bars_respects_start(seeded: Path) -> None:
    start = "2024-01-01T05:00:00Z"
    with TestClient(create_app(seeded, static_dir=None)) as client:
        bars = client.get(
            "/api/datasets/btcusdt/1h/bars", params={"start": start, "limit": 3}
        ).json()
    assert bars["time"][0] == int(datetime(2024, 1, 1, 5, tzinfo=UTC).timestamp())
    assert len(bars["time"]) == 3


def test_indicator_menu(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, static_dir=None)) as client:
        menu = {i["key"]: i for i in client.get("/api/indicators").json()}
    assert menu["rsi14"]["pane"] == "own"
    assert menu["rsi14"]["guides"] == [30, 70]
    assert menu["bollinger"]["lines"] == ["upper", "middle", "lower"]
    assert menu["sma200"]["pane"] == "price"


def test_bars_carry_indicators_warmed_up_on_earlier_history(seeded: Path) -> None:
    params: dict[str, Any] = {"limit": 10, "indicators": ["sma50", "rsi14"]}
    with TestClient(create_app(seeded, static_dir=None)) as client:
        bars = client.get("/api/datasets/BTCUSDT/1h/bars", params=params).json()
        refused = client.get("/api/datasets/BTCUSDT/1h/bars", params={"indicators": ["rsi7"]})
        plain = client.get("/api/datasets/BTCUSDT/1h/bars", params={"limit": 10}).json()
    sma = bars["indicators"]["sma50"]["value"]
    rsi = bars["indicators"]["rsi14"]["value"]
    assert len(sma) == len(rsi) == 10
    # 48 bars stored: the 10 shown are bars 38-47, so RSI 14 is warm and SMA 50 never is.
    assert all(v is not None for v in rsi)
    assert sma == [None] * 10
    assert refused.status_code == 422
    assert "unknown indicator" in refused.json()["detail"]
    assert plain["indicators"] == {}


def test_unknown_dataset_is_404(seeded: Path) -> None:
    with TestClient(create_app(seeded, static_dir=None)) as client:
        assert client.get("/api/datasets/ETHUSDT/1h/bars").status_code == 404
        assert client.get("/api/datasets/ETHUSDT/1h/quality").status_code == 404


def test_quality(seeded: Path) -> None:
    with TestClient(create_app(seeded, static_dir=None)) as client:
        q = client.get("/api/datasets/BTCUSDT/1h/quality").json()
    assert q["is_clean"] is True
    assert q["rows"] == 48


def test_ingest_job_end_to_end(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    months = [Month(2024, 1), Month(2024, 2)]
    app = create_app(
        tmp_path, client_factory=lambda: _mock_binance(make_archive, months), static_dir=None
    )
    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs/ingest",
            json={"symbol": "btcusdt", "interval": "1h", "start": "2024-01", "end": "2024-03"},
        )
        assert resp.status_code == 202
        job = _wait(client, resp.json()["id"])
        assert job["status"] == "succeeded", job
        assert job["result"]["fetched"] == ["2024-01", "2024-02"]
        assert job["result"]["not_found"] == ["2024-03"]
        assert job["progress"] == 1.0
        assert client.get("/api/datasets").json()[0]["months"] == 2


def test_failing_job_reports_error(tmp_path: Path) -> None:
    def broken() -> BinanceVisionClient:
        return BinanceVisionClient(
            httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
        )

    with TestClient(create_app(tmp_path, client_factory=broken, static_dir=None)) as client:
        job_id = client.post(
            "/api/jobs/ingest", json={"symbol": "BTCUSDT", "start": "2024-01", "end": "2024-01"}
        ).json()["id"]
        job = _wait(client, job_id)
    assert job["status"] == "failed"
    assert job["error"]


def test_ingest_with_nothing_published_fails_loudly(
    tmp_path: Path, make_archive: ArchiveFactory
) -> None:
    app = create_app(
        tmp_path, client_factory=lambda: _mock_binance(make_archive, []), static_dir=None
    )
    with TestClient(app) as client:
        job_id = client.post(
            "/api/jobs/ingest", json={"symbol": "NOPEUSDT", "start": "2024-01", "end": "2024-02"}
        ).json()["id"]
        job = _wait(client, job_id)
    assert job["status"] == "failed"
    assert "publishes no NOPEUSDT" in job["error"]


@pytest.mark.parametrize(
    "body",
    [
        {"symbol": "BTC/USDT", "start": "2024-01"},
        {"symbol": "BTCUSDT", "start": "Jan 2024"},
        {"symbol": "BTCUSDT", "start": "2024-05", "end": "2024-01"},
        {"symbol": "BTCUSDT", "start": "2024-01", "interval": "7m"},
    ],
)
def test_ingest_rejects_bad_input(tmp_path: Path, body: dict[str, str]) -> None:
    with TestClient(create_app(tmp_path, static_dir=None)) as client:
        assert client.post("/api/jobs/ingest", json=body).status_code == 422


def test_unknown_job_is_404(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path, static_dir=None)) as client:
        assert client.get("/api/jobs/nope").status_code == 404
        assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_jobs_are_listed_and_can_be_cancelled(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    gate = threading.Event()

    def slow_factory() -> BinanceVisionClient:
        gate.wait(5)
        return _mock_binance(make_archive, [Month(2024, 1)])

    app = create_app(tmp_path, client_factory=slow_factory, static_dir=None)
    with TestClient(app) as client:
        job = client.post(
            "/api/jobs/ingest", json={"symbol": "BTCUSDT", "start": "2024-01", "end": "2024-01"}
        ).json()
        assert [j["id"] for j in client.get("/api/jobs").json()] == [job["id"]]
        cancelled = client.post(f"/api/jobs/{job['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["message"] == "Cancelling"
        gate.set()
        assert _wait(client, job["id"])["status"] == "cancelled"


def test_built_dashboard_is_served(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<title>Kestrel</title>")
    with TestClient(create_app(tmp_path, static_dir=static)) as client:
        page = client.get("/")
    assert page.status_code == 200
    assert "Kestrel" in page.text


def test_server_reads_data_and_config_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    import kestrel.api.server as server

    config = Path(__file__).resolve().parent.parent / "kestrel.toml"
    monkeypatch.setenv("KESTREL_DATA", str(tmp_path))
    monkeypatch.setenv("KESTREL_CONFIG", str(config))
    with TestClient(importlib.reload(server).app) as client:
        assert client.get("/api/health").json()["data_dir"] == str(tmp_path.resolve())
        assert client.get("/api/costs").status_code == 200


def test_requests_for_another_host_are_refused(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kestrel.api.app import create_app

    app = create_app(tmp_path, static_dir=None)
    assert TestClient(app).get("/api/health").status_code == 200
    local = TestClient(app, base_url="http://127.0.0.1:8000")
    assert local.get("/api/health").status_code == 200
    # A page whose domain was rebound to 127.0.0.1 still sends its own name.
    rebound = TestClient(app, base_url="http://evil.example:8000")
    assert rebound.get("/api/health").status_code == 400
    assert rebound.post("/api/jobs/regimes", json={"symbol": "BTCUSDT"}).status_code == 400
