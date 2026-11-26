"""The read caches behind the dashboard's slow lists (2026-11-26): each file is read once
and again only when it changes, so a list never serves stale data."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kestrel.backtest.runs import RunStore
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.validation.log import Look, Study, ValidationLog
from tests.backtest.helpers import bars

DAY = timedelta(days=1)


def test_coverage_follows_the_stored_files(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    first = datetime(2024, 1, 1, tzinfo=UTC)
    store.write("BTCUSDT", Interval.D1, bars([1.0] * 40, start=first, step=DAY))
    c = store.coverage("BTCUSDT", Interval.D1)
    assert (c.rows, c.first, c.last, c.months) == (40, first, first + 39 * DAY, 2)
    assert store.coverage("BTCUSDT", Interval.D1) == c  # served from the cache
    store.write("BTCUSDT", Interval.D1, bars([1.0] * 70, start=first, step=DAY))  # into March
    grown = store.coverage("BTCUSDT", Interval.D1)
    assert (grown.rows, grown.last, grown.months) == (70, first + 69 * DAY, 3)
    empty = store.coverage("ETHUSDT", Interval.D1)
    assert (empty.rows, empty.first, empty.months) == (0, None, 0)


def _fake_run(root: Path, run_id: str, created: str, sharpe: float) -> Path:
    path = root / "runs" / run_id / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": run_id, "created_at": created, "config": {"big": 1},
                                "metrics": {"sharpe": sharpe}}))  # fmt: skip
    return path


def test_run_summaries_see_new_changed_and_deleted_runs(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _fake_run(tmp_path, "20260101-000000-aaaaaa", "2026-01-01", 1.0)
    (only,) = store.summaries()
    assert "config" not in only
    b = _fake_run(tmp_path, "20260102-000000-bbbbbb", "2026-01-02", 2.0)
    assert [r["metrics"]["sharpe"] for r in store.summaries()] == [2.0, 1.0]
    b.write_text(b.read_text().replace('"sharpe": 2.0', '"sharpe": 3.0'))
    os.utime(b, ns=(b.stat().st_atime_ns, b.stat().st_mtime_ns + 1_000_000))
    assert [r["metrics"]["sharpe"] for r in store.summaries()] == [3.0, 1.0]
    shutil.rmtree(b.parent)
    assert [r["id"] for r in store.summaries()] == ["20260101-000000-aaaaaa"]


def test_studies_see_new_and_changed_files(tmp_path: Path) -> None:
    log = ValidationLog(tmp_path)
    first = log.save(Study("robustness", "s", "BTCUSDT", "1d", "2020-01", "2025-10",
                           [Look({"a": 1}, 0.5)], "ROBUST"))  # fmt: skip
    assert [s["id"] for s in log.studies()] == [first]
    path = tmp_path / "validation" / f"{first}.json"
    doc = json.loads(path.read_text())
    doc["verdict"] = "FRAGILE, rewritten"
    path.write_text(json.dumps(doc))
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    assert log.studies()[0]["verdict"] == "FRAGILE, rewritten"
