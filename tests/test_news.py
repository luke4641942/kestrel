"""News tone from GDELT: patient fetching, resuming, and the news-tone signal's timing."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data import news
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.studies.weekly_signal import news_flags
from tests.test_volhold_weekly import daily

REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")


class FakeGdelt:
    def __init__(self, refusals: int = 0) -> None:
        self.refusals = refusals
        self.months: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.refusals:
            self.refusals -= 1
            return httpx.Response(429, text="Please limit requests to one every 5 seconds")
        start = datetime.strptime(request.url.params["startdatetime"][:8], "%Y%m%d").date()
        end = datetime.strptime(request.url.params["enddatetime"][:8], "%Y%m%d").date()
        self.months.append(f"{start:%Y-%m}")
        data = [
            {"date": f"{start + timedelta(days=i):%Y%m%d}T000000Z", "value": -1.0 + i / 100}
            for i in range((end - start).days + 1)
        ]
        return httpx.Response(200, json={"timeline": [{"data": data}]})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def no_wait(_: float) -> None:
    pass


def test_a_month_backs_off_when_refused_then_gives_up() -> None:
    waits: list[float] = []
    fake = FakeGdelt(refusals=2)
    month = news.fetch_month("bitcoin", date(2018, 2, 1), fake.client(), waits.append)
    assert waits == list(news.BACKOFF[:2])
    assert month.height == 28  # February 2018
    assert month["tone"][0] == pytest.approx(-1.0)
    with pytest.raises(news.NewsError, match="kept refusing 2018-02"):
        news.fetch_month("bitcoin", date(2018, 2, 1), FakeGdelt(refusals=99).client(), no_wait)


def test_odd_replies_are_reported() -> None:
    def text(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Invalid query")

    with pytest.raises(news.NewsError, match="no data for 2018-02"):
        news.fetch_month(
            "x", date(2018, 2, 1), httpx.Client(transport=httpx.MockTransport(text)), no_wait
        )

    def empty(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"timeline": []})

    got = news.fetch_month(
        "x", date(2018, 2, 1), httpx.Client(transport=httpx.MockTransport(empty)), no_wait
    )
    assert got.is_empty()


def test_ingest_saves_as_it_goes_and_resumes(tmp_path: Path) -> None:
    fake = FakeGdelt()
    pauses: list[float] = []
    news.ingest("btcusdt", fake.client(), tmp_path, end=date(2017, 3, 15), sleep=pauses.append)
    assert fake.months == ["2017-01", "2017-02", "2017-03"]
    assert pauses == [news.PAUSE, news.PAUSE]  # a pause between requests, not before the first
    later = FakeGdelt()
    frame = news.ingest("BTCUSDT", later.client(), tmp_path, end=date(2017, 4, 2), sleep=no_wait)
    assert later.months == ["2017-04"]  # only what wasn't stored
    assert frame["date"].n_unique() == frame.height
    assert news.load(tmp_path, "BTCUSDT").height == frame.height
    with pytest.raises(news.NewsError, match="no news query"):
        news.ingest("DOGEUSDT", fake.client(), tmp_path)
    with pytest.raises(news.NewsError, match="ingest-news"):
        news.load(tmp_path, "ETHUSDT")


def test_news_tone_uses_only_complete_days() -> None:
    bars = daily([0.0] * 20)
    start = date(2023, 11, 20)
    tone = [-2.0] * 70
    t = 10  # the close of 2024-01-11
    day_t = (date(2024, 1, 1) + timedelta(days=t) - start).days
    today = tone.copy()
    today[day_t] = 50.0  # not complete at the close: must not count
    yesterday = tone.copy()
    yesterday[day_t - 1] = 50.0  # complete the day before: counts
    frame = lambda values: pl.DataFrame(  # noqa: E731
        {"date": [start + timedelta(days=i) for i in range(len(values))], "tone": values},
        schema=news.SCHEMA,
    )
    assert news_flags(bars, frame(today))[t] is False
    assert news_flags(bars, frame(yesterday))[t] is True
    assert news_flags(bars, frame([-2.0] * 5))[t] is None


def test_the_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import kestrel.cli as cli

    client = FakeGdelt().client()
    monkeypatch.setattr(cli.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(news.time, "sleep", no_wait)
    monkeypatch.setattr(news, "FIRST", date(2025, 1, 1))
    got = CliRunner().invoke(app, ["ingest-news", "btcusdt", "--data-dir", str(tmp_path)])
    assert got.exit_code == 0, got.output
    assert "  2025-01" in got.output
    assert "days of news tone" in got.output
    assert (
        CliRunner().invoke(app, ["ingest-news", "DOGEUSDT", "--data-dir", str(tmp_path)]).exit_code
        == 1
    )

    start = datetime(2025, 3, 1, tzinfo=UTC)
    bars = daily([0.01 * (1 if i % 14 < 7 else -1) for i in range(200)])
    bars = bars.with_columns(pl.col("open_time") - (bars["open_time"][0] - start))
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, bars)
    args = [
        "signal-study",
        "news-tone",
        "BTCUSDT",
        "--data-dir",
        str(tmp_path),
        "--config",
        REPO_CONFIG,
    ]
    study = CliRunner().invoke(app, args)
    assert study.exit_code == 0, study.output
    assert "Signal study  ·  news-tone  ·  BTCUSDT" in study.output
