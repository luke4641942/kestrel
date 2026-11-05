from __future__ import annotations

import re
from datetime import UTC, datetime
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


def plain(output: str) -> str:
    """CLI output without colour codes, error-box borders or line wrapping.

    On CI the CLI's error box is coloured and wrapped at 80 columns, which splits long
    messages across lines; tests should check the message, not the terminal layout."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", output)
    text = re.sub(r"[│╭╮╰╯─]", " ", text)
    return " ".join(text.split())


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


REPO_CONFIG = str(Path(__file__).resolve().parent.parent / "kestrel.toml")


def test_backtest_prints_a_report(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, parse_archive(make_archive(n=24 * 10)))
    args = ["backtest", "BTCUSDT", "--start", "2024-01", "--end", "2024-01", "--interval", "1h"]
    result = runner.invoke(app, [*args, "--data-dir", str(tmp_path), "--config", REPO_CONFIG])
    assert result.exit_code == 0, result.output
    assert "buy-and-hold BTCUSDT" in result.output
    assert "P(true Sharpe > 0)" in result.output
    assert "too few to trust" in result.output


def test_backtest_no_save_leaves_no_run(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, parse_archive(make_archive(n=24 * 10)))
    args = ["backtest", "BTCUSDT", "--start", "2024-01", "--end", "2024-01", "--interval", "1h"]
    result = runner.invoke(
        app, [*args, "--no-save", "--data-dir", str(tmp_path), "--config", REPO_CONFIG]
    )
    assert result.exit_code == 0, result.output
    assert "Saved as run" not in result.output
    assert not (tmp_path / "runs").exists()


def test_backtest_saves_by_default(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, parse_archive(make_archive(n=24 * 10)))
    args = ["backtest", "BTCUSDT", "--start", "2024-01", "--end", "2024-01", "--interval", "1h"]
    result = runner.invoke(app, [*args, "--data-dir", str(tmp_path), "--config", REPO_CONFIG])
    assert "Saved as run" in result.output
    assert len(list((tmp_path / "runs").glob("*/run.json"))) == 1


def test_backtest_refuses_holdout(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    archive = make_archive(start=datetime(2025, 11, 1, tzinfo=UTC), n=24 * 5)
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, parse_archive(archive))
    args = ["backtest", "BTCUSDT", "--start", "2025-11", "--end", "2025-11", "--interval", "1h"]
    result = runner.invoke(app, [*args, "--data-dir", str(tmp_path), "--config", REPO_CONFIG])
    assert result.exit_code == 1
    assert "holdout period" in result.output


def test_backtest_unknown_strategy(tmp_path: Path) -> None:
    args = ["backtest", "BTCUSDT", "--start", "2024-01", "--end", "2024-01", "--strategy", "magic"]
    result = runner.invoke(app, [*args, "--data-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "unknown strategy 'magic'" in plain(result.output)


def test_serve_passes_data_and_config_to_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr("uvicorn.run", lambda target, **kw: calls.append((target, kw)))
    monkeypatch.delenv("KESTREL_DATA", raising=False)
    monkeypatch.delenv("KESTREL_CONFIG", raising=False)
    result = runner.invoke(
        app, ["serve", "--data-dir", str(tmp_path), "--config", "other.toml", "--port", "9001"]
    )
    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "kestrel.api.server:app",
            {"host": "127.0.0.1", "port": 9001, "reload": False, "factory": False},
        )
    ]
    import os

    assert os.environ["KESTREL_DATA"] == str(tmp_path)
    assert os.environ["KESTREL_CONFIG"] == "other.toml"
    assert os.environ["KESTREL_RESEARCH"] == "research"


def test_backtest_with_no_data(tmp_path: Path) -> None:
    args = ["backtest", "BTCUSDT", "--start", "2024-01", "--end", "2024-02"]
    result = runner.invoke(app, [*args, "--data-dir", str(tmp_path), "--config", REPO_CONFIG])
    assert result.exit_code == 1
    assert "no stored BTCUSDT 4h bars" in result.output


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
def test_ingest_fails_when_nothing_is_published(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "ingest",
            "BTCUSDT",
            "--start",
            "2023-01",
            "--end",
            "2023-02",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "Binance publishes no BTCUSDT 1h data for 2023-01..2023-02" in result.output
    assert ParquetStore(tmp_path).datasets() == []


@pytest.mark.usefixtures("offline_binance")
def test_ingest_then_check_is_clean(tmp_path: Path) -> None:
    args = ["--data-dir", str(tmp_path)]
    runner.invoke(app, ["ingest", "BTCUSDT", "--start", "2024-01", "--end", "2024-01", *args])
    result = runner.invoke(app, ["check", "BTCUSDT", *args])
    assert result.exit_code == 0
    assert "rows:               24" in result.output


def _hourly_store(tmp_path: Path, make_archive: ArchiveFactory) -> list[str]:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, parse_archive(make_archive(n=24 * 10)))
    return [
        "backtest", "BTCUSDT", "--start", "2024-01", "--end", "2024-01", "--interval", "1h",
        "--data-dir", str(tmp_path), "--config", REPO_CONFIG,
        "--research-dir", str(tmp_path / "research"),
    ]  # fmt: skip


def test_backtest_with_params(tmp_path: Path, make_archive: ArchiveFactory) -> None:
    args = _hourly_store(tmp_path, make_archive)
    result = runner.invoke(
        app, [*args, "--strategy", "ma-trend", "--param", "fast=3", "--param", "slow=12"]
    )
    assert result.exit_code == 0, result.output
    assert "moving-average trend 3/12" in result.output


@pytest.mark.parametrize(
    ("param", "message"),
    [("fast", "name=<whole number>"), ("fast=x", "name=<whole number>"), ("fast=0", "2 to 200")],
)
def test_backtest_bad_params(
    tmp_path: Path, make_archive: ArchiveFactory, param: str, message: str
) -> None:
    args = _hourly_store(tmp_path, make_archive)
    result = runner.invoke(app, [*args, "--strategy", "ma-trend", "--param", param])
    assert result.exit_code == 2
    assert message in plain(result.output)


def test_backtest_research_requires_a_frozen_note(
    tmp_path: Path, make_archive: ArchiveFactory
) -> None:
    from kestrel.research import SECTIONS, ResearchStore

    args = _hourly_store(tmp_path, make_archive)
    notes = ResearchStore(tmp_path / "research")
    slug = notes.create("Trend").slug
    refused = runner.invoke(app, [*args, "--research", slug])
    assert refused.exit_code == 2
    assert "freeze" in plain(refused.output)

    notes.edit(slug, 1, {k: "x" for k in SECTIONS}, "ma-trend")
    notes.freeze(slug, 1)
    result = runner.invoke(
        app, [*args, "--research", slug, "--param", "fast=3", "--param", "slow=12"]
    )
    assert result.exit_code == 0, result.output
    assert f"Logged as a trial of {slug} v1" in result.output
    assert notes.get(slug).status == "backtested"
