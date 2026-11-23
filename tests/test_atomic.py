"""Atomic writes that concurrent writers can share (kestrel.atomic). The paper accounts run
as separate jobs at the same minute and refresh the same market; with one fixed temporary
name they took each other's files away (FileNotFoundError, 2026-11-21 to 23)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from kestrel.atomic import temp_path, write_atomically
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from tests.backtest.helpers import bars


def test_every_write_gets_its_own_hidden_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "a.parquet"
    first, second = temp_path(target), temp_path(target)
    assert first != second
    assert first.parent == target.parent
    assert first.name.startswith(".a.parquet.")
    assert not first.name.endswith(".parquet")  # never matched by month=*.parquet


def test_a_failed_write_leaves_no_temporary_file_and_the_old_target(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old")

    def broken(tmp: Path) -> None:
        tmp.write_text("half")
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        write_atomically(target, broken)
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_jobs_writing_the_same_month_at_once_never_crash(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    frame = bars([100.0 + i for i in range(30)])

    def write(_: int) -> None:
        store.write("ETHUSDT", Interval.D1, frame)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(64)))  # raised FileNotFoundError with a fixed tmp name
    assert store.read("ETHUSDT", Interval.D1).height == 30
    month_dir = next((tmp_path / "klines").rglob("interval=1d"))
    assert not [p for p in month_dir.iterdir() if p.name.endswith(".tmp")]
