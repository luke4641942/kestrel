from __future__ import annotations

from pathlib import Path

import httpx
import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data.binance_vision import BinanceVisionClient, Month, archive_url, parse_archive
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from tests.conftest import ArchiveFactory, checksum_for

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


def test_list_with_no_data(tmp_path: Path) -> None:
    result = runner.invoke(app, ["list", "BTCUSDT", "--data-dir", str(tmp_path)])
    assert result.output.strip() == "(no data)"


@pytest.fixture
def offline_binance(monkeypatch: pytest.MonkeyPatch, make_archive: ArchiveFactory) -> None:
    """Route the CLI's Binance client to a mock that only publishes 2024-01."""
    payload = make_archive()
    url = archive_url("BTCUSDT", Interval.H1, Month(2024, 1))
    routes = {
        url: httpx.Response(200, content=payload),
        url + ".CHECKSUM": httpx.Response(200, text=checksum_for(payload, "f.zip")),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "kestrel.cli.BinanceVisionClient",
        lambda: BinanceVisionClient(httpx.Client(transport=transport)),
    )


@pytest.mark.usefixtures("offline_binance")
def test_ingest_stores_fetched_months_and_reports_missing(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "ingest",
            "BTCUSDT",
            "--start",
            "2024-01",
            "--end",
            "2024-02",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "fetched 1 month(s) of BTCUSDT 1h" in result.output
    assert "not published by Binance: 2024-02" in result.output
    assert ParquetStore(tmp_path).months("BTCUSDT", Interval.H1) == ["2024-01"]


@pytest.mark.usefixtures("offline_binance")
def test_ingest_then_check_is_clean(tmp_path: Path) -> None:
    args = ["--data-dir", str(tmp_path)]
    runner.invoke(app, ["ingest", "BTCUSDT", "--start", "2024-01", "--end", "2024-01", *args])
    result = runner.invoke(app, ["check", "BTCUSDT", *args])
    assert result.exit_code == 0
    assert "rows:               24" in result.output
