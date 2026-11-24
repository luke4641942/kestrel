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


def test_a_limited_run_fetches_a_few_months_and_the_next_carries_on(tmp_path: Path) -> None:
    first, second = FakeGdelt(), FakeGdelt()
    end = date(2017, 5, 15)
    news.ingest("BTCUSDT", first.client(), tmp_path, end=end, sleep=no_wait, limit=2)
    assert first.months == ["2017-01", "2017-02"]
    news.ingest("BTCUSDT", second.client(), tmp_path, end=end, sleep=no_wait, limit=2)
    assert second.months == ["2017-03", "2017-04"]


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


def test_a_gentle_fetch_tries_once_and_says_it_was_refused() -> None:
    fake = FakeGdelt(refusals=5)
    with pytest.raises(news.NewsRefusedError):
        news.fetch_month("bitcoin", date(2018, 2, 1), fake.client(), sleep=no_wait, backoff=())
    assert fake.refusals == 4  # one request, no retries


def test_the_market_furthest_behind_goes_first(tmp_path: Path) -> None:
    news.ingest("BTCUSDT", FakeGdelt().client(), tmp_path, end=date(2017, 3, 15), sleep=no_wait)
    news.ingest("ETHUSDT", FakeGdelt().client(), tmp_path, end=date(2017, 1, 15), sleep=no_wait)
    assert news.furthest_behind(["BTCUSDT", "ETHUSDT"], tmp_path) == ["ETHUSDT", "BTCUSDT"]
    assert news.furthest_behind(["ETHUSDT", "BTCUSDT"], tmp_path / "none") == ["BTCUSDT", "ETHUSDT"]


def test_a_scheduled_run_waits_out_a_refusal_instead_of_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import kestrel.cli as cli

    news.ingest("BTCUSDT", FakeGdelt().client(), tmp_path, end=date(2017, 2, 15), sleep=no_wait)
    asked: list[str] = []
    fake = FakeGdelt(refusals=2)  # the first request and its one retry are refused

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["query"])
        return fake.handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(cli.time, "sleep", no_wait)
    args = ["ingest-news", "BTCUSDT", "ETHUSDT", "--max-months", "1", "--data-dir", str(tmp_path)]
    got = CliRunner().invoke(app, args)
    assert got.exit_code == 0, got.output
    # ETH is further behind: first, tried and retried once, then left for the next run.
    assert asked == ["ethereum", "ethereum", "bitcoin"]
    assert "ETHUSDT: GDELT refused (rate limit); the next run tries again" in got.output
    assert "  2017-03" in got.output  # BTC carried on


def test_a_scheduled_run_retries_a_refusal_once_and_carries_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import kestrel.cli as cli

    fake = FakeGdelt(refusals=1)
    client = fake.client()  # built before httpx.Client is replaced
    waits: list[float] = []
    monkeypatch.setattr(cli.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(cli.time, "sleep", waits.append)
    args = ["ingest-news", "BTCUSDT", "--max-months", "3", "--data-dir", str(tmp_path)]
    got = CliRunner().invoke(app, args)
    assert got.exit_code == 0, got.output
    assert fake.months == ["2017-01", "2017-02", "2017-03"]  # recovered, then kept going
    assert waits == [30.0, news.PAUSE, news.PAUSE]  # never faster than one request per 10s


def test_the_study_waits_for_the_whole_download_and_controls_for_the_past_week(
    tmp_path: Path,
) -> None:
    """Its note pre-registers both: one look, on the whole history to the holdout, and the
    spread positive after rising and after falling weeks alike."""
    start = datetime(2025, 3, 1, tzinfo=UTC)
    bars = daily([0.01 * (1 if i % 14 < 7 else -1) for i in range(200)])
    bars = bars.with_columns(pl.col("open_time") - (bars["open_time"][0] - start))
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, bars)
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(303)]  # to 2025-10-30
    tone = pl.DataFrame({"date": days, "tone": [-1.0 + (i % 9) / 10 for i in range(303)]},
                        schema=news.SCHEMA)  # fmt: skip
    news.path(tmp_path, "BTCUSDT").parent.mkdir(parents=True, exist_ok=True)
    tone.write_parquet(news.path(tmp_path, "BTCUSDT"))
    args = ["signal-study", "news-tone", "BTCUSDT", "--data-dir", str(tmp_path),
            "--config", REPO_CONFIG]  # fmt: skip
    early = CliRunner().invoke(app, args)
    assert early.exit_code == 1
    assert "reaches 2025-10-30, not 2025-10-31; let kestrel ingest-news finish" in early.output
    last = pl.DataFrame({"date": [date(2025, 10, 31)], "tone": [0.0]}, schema=news.SCHEMA)
    pl.concat([tone, last]).write_parquet(news.path(tmp_path, "BTCUSDT"))
    done = CliRunner().invoke(app, args)
    assert done.exit_code == 0, done.output
    assert "Control (pre-registered)" in done.output
    with pytest.raises(news.NewsError, match="reaches nothing"):
        news.require_through(tone.head(0), "ethusdt", date(2025, 10, 31))
