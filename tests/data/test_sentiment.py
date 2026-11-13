"""The Fear & Greed history: parsing, storage, and when each value is known."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.data import sentiment
from kestrel.data.features import attach_features
from kestrel.data.schema import Interval
from tests.backtest.helpers import bars


def payload(values: dict[date, int]) -> dict[str, Any]:
    """The API's shape: newest first, timestamps in seconds at 00:00 UTC."""
    rows = [
        {
            "value": str(v),
            "value_classification": "Extreme Fear" if v <= 25 else "Neutral",
            "timestamp": str(int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())),
        }
        for d, v in sorted(values.items(), reverse=True)
    ]
    return {"name": "Fear and Greed Index", "data": rows, "metadata": {"error": None}}


D0 = date(2024, 1, 1)


def days(*values: int, start: date = D0) -> dict[date, int]:
    return {start + timedelta(days=i): v for i, v in enumerate(values)}


def test_parse_sorts_oldest_first_and_reports_gaps() -> None:
    values = days(30, 20, 50)
    values[D0 + timedelta(days=6)] = 70  # days 3, 4 and 5 missing
    history = sentiment.parse(payload(values))
    assert history.frame["date"].to_list() == sorted(values)
    assert history.frame["value"].to_list() == [30, 20, 50, 70]
    assert history.gaps == [(D0 + timedelta(days=3), D0 + timedelta(days=5))]


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ({"data": []}, "no data"),
        ({"data": ["x"]}, "malformed"),
        (
            {"data": [{"value": "10", "value_classification": "F", "timestamp": "1704070800"}]},
            "not at 00:00 UTC",
        ),
        (
            {"data": [{"value": "101", "value_classification": "F", "timestamp": "1704067200"}]},
            "outside 0-100",
        ),
        (
            {"data": [{"value": "10", "value_classification": "F", "timestamp": "1704067200"}] * 2},
            "two values published",
        ),
    ],
)
def test_malformed_histories_are_refused(doc: dict[str, Any], message: str) -> None:
    with pytest.raises(sentiment.SentimentError, match=message):
        sentiment.parse(doc)


def test_fetch_asks_for_the_whole_history() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload(days(40, 41)))

    history = sentiment.fetch(httpx.Client(transport=httpx.MockTransport(handler)))
    assert history.frame.height == 2
    assert seen[0].url.params["limit"] == "0"


def test_save_and_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ingest-sentiment"):
        sentiment.load(tmp_path)
    history = sentiment.parse(payload(days(40, 41)))
    stored = sentiment.save(history, tmp_path)
    assert sentiment.load(tmp_path).equals(history.frame)
    assert stored.with_suffix(".json").exists()


# ---------- when each value is known ----------


def daily_bars(n: int, start: date = D0) -> pl.DataFrame:
    return bars(
        [100.0] * n,
        start=datetime(start.year, start.month, start.day, tzinfo=UTC),
        step=timedelta(days=1),
    )


def test_a_daily_bar_uses_its_own_days_value_never_the_next() -> None:
    # The bar opening on day D closes at D+1 00:00, when day D's value is known; day D+1's
    # value isn't known until D+2.
    history = sentiment.parse(payload(days(10, 20, 30, 40))).frame
    attached = sentiment.attach(daily_bars(4), history, timedelta(days=1))
    assert attached["fear_greed"].to_list() == [10, 20, 30, 40]


def test_an_intraday_bar_uses_only_yesterdays_value() -> None:
    history = sentiment.parse(payload(days(10, 20, 30))).frame
    four_hourly = bars([100.0] * 6, start=datetime(2024, 1, 2, tzinfo=UTC), step=timedelta(hours=4))
    attached = sentiment.attach(four_hourly, history, timedelta(hours=4))
    # Every 4h bar of 2 January closes before 3 January, so only 1 January's value (10) is
    # known, until the last bar, which closes at 3 January 00:00, when 2 January's (20) is.
    assert attached["fear_greed"].to_list() == [10, 10, 10, 10, 10, 20]


def test_a_stale_value_is_unknown_not_carried_forward() -> None:
    values = days(10, 20)
    values[D0 + timedelta(days=8)] = 90
    history = sentiment.parse(payload(values)).frame
    attached = sentiment.attach(daily_bars(10), history, timedelta(days=1))
    readings = attached["fear_greed"].to_list()
    assert readings[:2] == [10, 20]
    assert readings[2:5] == [20, 20, 20]  # up to 3 days old: still the latest known
    assert readings[5:8] == [None, None, None]  # older than that: unknown
    assert readings[8] == 90


def test_bars_before_the_history_starts_have_no_reading() -> None:
    history = sentiment.parse(payload(days(10, start=D0 + timedelta(days=5)))).frame
    attached = sentiment.attach(daily_bars(7), history, timedelta(days=1))
    assert attached["fear_greed"].to_list()[:5] == [None] * 5


def test_attach_features(tmp_path: Path) -> None:
    sentiment.save(sentiment.parse(payload(days(10, 20))), tmp_path)
    out = attach_features(daily_bars(2), ("fear_greed",), "BTCUSDT", Interval.D1, tmp_path)
    assert out["fear_greed"].to_list() == [10, 20]
    assert (
        attach_features(daily_bars(2), (), "BTCUSDT", Interval.D1, tmp_path).columns
        == daily_bars(2).columns
    )
    with pytest.raises(ValueError, match="unknown feature 'moon_phase'"):
        attach_features(daily_bars(2), ("moon_phase",), "BTCUSDT", Interval.D1, tmp_path)


# ---------- the command line ----------


def test_the_cli_stores_the_history_and_reports_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import kestrel.cli as cli

    values = days(10, 20)
    values[D0 + timedelta(days=4)] = 30
    monkeypatch.setattr(cli.sentiment, "fetch", lambda: sentiment.parse(payload(values)))
    result = CliRunner().invoke(cli.app, ["ingest-sentiment", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "3 days of Fear & Greed, 2024-01-01 to 2024-01-05" in result.output
    assert "gap: 2024-01-03 to 2024-01-04 (2 days not published)" in result.output


def test_the_cli_reports_a_failed_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import kestrel.cli as cli

    def broken() -> sentiment.SentimentHistory:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(cli.sentiment, "fetch", broken)
    result = CliRunner().invoke(cli.app, ["ingest-sentiment", "--data-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "couldn't fetch" in result.output
