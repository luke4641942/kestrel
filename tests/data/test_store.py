from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from kestrel.data.binance_vision import parse_archive
from kestrel.data.schema import KLINE_SCHEMA, Interval
from kestrel.data.store import ParquetStore
from tests.conftest import ArchiveFactory


def test_round_trip(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    df = parse_archive(make_archive(n=48))
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.H1, df)
    assert store.read("BTCUSDT", Interval.H1).equals(df)


def test_partitions_by_month(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    # 72 hourly bars starting 2024-01-31 spans into February.
    df = parse_archive(make_archive(start=datetime(2024, 1, 31, tzinfo=UTC), n=72))
    store = ParquetStore(tmp_path)
    paths = store.write("BTCUSDT", Interval.H1, df)
    assert [p.name for p in paths] == ["month=2024-01.parquet", "month=2024-02.parquet"]
    assert store.months("BTCUSDT", Interval.H1) == ["2024-01", "2024-02"]


def test_rewrite_is_idempotent(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    df = parse_archive(make_archive(n=24))
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.H1, df)
    store.write("BTCUSDT", Interval.H1, df)
    assert store.read("BTCUSDT", Interval.H1).height == 24


def test_range_filter_is_half_open(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.H1, parse_archive(make_archive(start=start, n=24)))
    out = store.read("BTCUSDT", Interval.H1, start + timedelta(hours=2), start + timedelta(hours=5))
    assert out["open_time"].to_list() == [start + timedelta(hours=h) for h in (2, 3, 4)]


def test_empty_store_returns_typed_empty_frame(tmp_path: Path) -> None:
    out = ParquetStore(tmp_path).read("ETHUSDT", Interval.H1)
    assert out.is_empty()
    assert out.schema == pl.Schema(KLINE_SCHEMA)


def test_writing_empty_frame_is_a_no_op(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    assert store.write("BTCUSDT", Interval.H1, pl.DataFrame(schema=KLINE_SCHEMA)) == []
    assert store.datasets() == []


def test_no_temp_files_left_behind(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, parse_archive(make_archive()))
    assert not list(tmp_path.rglob("*.tmp"))
