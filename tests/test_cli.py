from __future__ import annotations

from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data.binance_vision import parse_archive
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from tests.conftest import ArchiveFactory

runner = CliRunner()


def test_check_passes_on_clean_data(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, parse_archive(make_archive()))
    result = runner.invoke(app, ["check", "BTCUSDT", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "CLEAN" in result.output


def test_check_fails_on_corrupt_data(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    df = parse_archive(make_archive())
    corrupt = df.with_columns((pl.col("low") * 0.5).alias("high"))  # high below low
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, corrupt)
    result = runner.invoke(app, ["check", "BTCUSDT", "--data-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_list_shows_months(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, parse_archive(make_archive()))
    result = runner.invoke(app, ["list", "BTCUSDT", "--data-dir", str(tmp_path)])
    assert result.output.strip() == "2024-01"
