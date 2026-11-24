"""The public demo's snapshot (kestrel.api.demo): what it holds, how calls map to files,
thinning, and the refusal to publish a local path or a banned term."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kestrel.api import demo
from kestrel.api.app import create_app
from kestrel.cli import app
from kestrel.config import load_config
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.execution.paper import PaperAccount, Terms, run
from tests.backtest.helpers import T0
from tests.test_paper import walk

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"

# The same cases are in frontend/src/demo.test.ts: both sides must name a call's file alike.
FILE_CASES = {
    "/paper": "paper.json",
    "/backtests/20261122-203214-573b7c": "backtests/20261122-203214-573b7c.json",
    "/strategies/fx:trend/source": "strategies/fx_trend/source.json",
    "/datasets/BTCUSDT/1d/bars?limit=20000&indicators=sma": "datasets/BTCUSDT/1d/bars.json",
    "/validation/history": "validation/history.json",
}


@pytest.mark.parametrize(("path", "name"), FILE_CASES.items())
def test_calls_map_to_files(path: str, name: str) -> None:
    assert demo.file_for(path.split("?")[0]) == name


def test_long_series_are_thinned_evenly_keeping_both_ends() -> None:
    n = 10_001
    series = {"time": list(range(n)), "equity": [float(i) for i in range(n)], "label": "x"}
    thinned = demo.thin({"equity": series, "metrics": {"sharpe": 1.2}}, limit=101)
    t = thinned["equity"]["time"]
    assert len(t) == 101
    assert (t[0], t[-1]) == (0, n - 1)
    assert thinned["equity"]["equity"] == [float(i) for i in t]  # columns stay aligned
    assert thinned["equity"]["label"] == "x"
    assert thinned["metrics"] == {"sharpe": 1.2}
    short = {"time": [1, 2], "equity": [1.0, 2.0]}
    assert demo.thin(short, limit=101) == short


def data_dir(tmp_path: Path) -> Path:
    frame = walk(200)
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, frame)
    terms = Terms("tvt", "trend-vol-target", "BTCUSDT", Interval.D1, {}, Decimal(330), T0)
    run(PaperAccount.open(tmp_path, terms), frame.head(150), load_config(REPO_CONFIG))
    return tmp_path


def test_the_snapshot_is_the_apis_reads_without_local_details(tmp_path: Path) -> None:
    client = TestClient(create_app(data_dir(tmp_path), static_dir=None, config_path=REPO_CONFIG))
    snap = demo.collect(client)
    files = snap.files
    assert files["health.json"]["data_dir"] == "demo"
    assert files["jobs.json"] == []
    assert files["paper/tvt.json"]["name"] == "tvt"
    bars = files["datasets/BTCUSDT/1d/bars.json"]
    assert len(bars["time"]) == 200  # every stored bar...
    assert set(bars["indicators"]) == {i["key"] for i in files["indicators.json"]}  # ...and line
    assert "datasets/BTCUSDT/1d/quality.json" in files
    assert "datasets/ETHUSDT/1d/bars.json" not in files  # not stored: not asked for
    assert "strategies/trend-vol-target/source.json" in files
    assert snap.missing == []

    demo.check(snap, [], "/nowhere")  # clean
    version = files["health.json"]["version"]  # found only inside health.json
    with pytest.raises(demo.DemoError, match=r"health\.json") as e:
        demo.check(snap, [version.upper()], "/nowhere")  # any case
    assert version not in str(e.value)  # the message never repeats the term
    demo.check(snap, [], str(tmp_path))  # the API never sends the data folder's path...
    snap.files["planted.json"] = {"log": f"{tmp_path}/data/runs"}
    with pytest.raises(demo.DemoError, match=r"planted\.json"):
        demo.check(snap, [], str(tmp_path))  # ...and one that did would stop the export


def test_the_command(tmp_path: Path) -> None:
    data = data_dir(tmp_path / "data")
    out = tmp_path / "out"
    (out / "stale").mkdir(parents=True)
    common = ["--data-dir", str(data), "--config", str(REPO_CONFIG)]
    done = CliRunner().invoke(app, ["demo-export", str(out), *common])
    assert done.exit_code == 0, done.output
    assert not (out / "stale").exists()  # emptied first
    meta = json.loads((out / "meta.json").read_text())
    assert meta["files"] == len(list((out / "api").rglob("*.json")))
    assert json.loads((out / "api" / "paper.json").read_text())[0]["name"] == "tvt"

    (data / demo.BANNED_FILE).write_text("tvt\n")
    refused = CliRunner().invoke(app, ["demo-export", str(out), *common])
    assert refused.exit_code == 1
    assert "banned term or local path" in refused.output
