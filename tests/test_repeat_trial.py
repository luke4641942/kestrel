"""An unchanged setup saved again under a newer version of its note would count as a new
trial (the version is in the trial key); the CLI refuses it without --as-new-trial and the
API refuses it outright (findings 33)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kestrel.api.app import create_app
from kestrel.backtest.runs import RunStore
from kestrel.cli import app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.research import SECTIONS, ResearchStore
from tests.test_warmup import REPO_CONFIG, rising


def test_an_unchanged_setup_under_a_new_version_is_refused(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, rising(datetime(2021, 1, 1, tzinfo=UTC)))
    notes = ResearchStore(tmp_path / "r")
    slug = notes.create("tvt").slug
    notes.edit(slug, 1, {k: "x" for k in SECTIONS}, "trend-vol-target")
    notes.freeze(slug, 1)
    args = ["backtest", "BTCUSDT", "--strategy", "trend-vol-target", "--research", slug,
            "--interval", "1d", "--start", "2022-01", "--end", "2022-12",
            "--data-dir", str(tmp_path), "--config", str(REPO_CONFIG),
            "--research-dir", str(tmp_path / "r")]  # fmt: skip
    runner = CliRunner()
    assert runner.invoke(app, args).exit_code == 0
    assert runner.invoke(app, args).exit_code == 0  # the same version again: the same trial
    notes.new_version(slug, "adds a walk-forward")
    notes.freeze(slug, 2)
    refused = runner.invoke(app, args)
    assert refused.exit_code == 1
    assert "would count as a new trial" in refused.output
    assert "--as-new-trial" in refused.output
    assert runner.invoke(app, [*args, "--param", "slow_days=150"]).exit_code == 0  # new setup
    assert runner.invoke(app, [*args, "--as-new-trial"]).exit_code == 0
    versions = sorted(r["research"]["version"] for r in RunStore(tmp_path).summaries())
    assert versions == [1, 1, 2, 2]

    client = TestClient(
        create_app(data_dir=tmp_path, config_path=REPO_CONFIG, research_dir=tmp_path / "r")
    )
    body = {"strategy": "trend-vol-target", "research": slug, "symbol": "BTCUSDT",
            "interval": "1d", "start": "2022-01", "end": "2022-06",
            "initial_cash": 330}  # fmt: skip
    assert client.post("/api/jobs/backtest", json=body).status_code == 202  # never run before
    notes.new_version(slug, "another change")
    notes.freeze(slug, 3)
    no = client.post("/api/jobs/backtest", json={**body, "end": "2022-12"})
    assert no.status_code == 422
    assert "From the command line, rerun with --as-new-trial" in no.text
