"""Attention data and the weekly signal studies (attention, extreme fear), offline."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data import attention
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.studies.volatile_direction import Sample, compare
from kestrel.studies.weekly_signal import (
    SignalResult,
    attention_flags,
    fear_flags,
    format_signal_study,
    momentum_overlap,
    run_signal_study,
    sunday_samples,
)
from tests.test_volhold_weekly import daily

REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")
D0 = date(2024, 1, 1)  # a Monday; the bars from tests.test_volhold_weekly start here too


def views(values: list[int], start: date = D0 - timedelta(days=40)) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": [start + timedelta(days=i) for i in range(len(values))], "views": values},
        schema=attention.SCHEMA,
    )


class FakeWikimedia:
    def __init__(self, days: int = 60) -> None:
        self.agents: list[str] = []
        self.days = days

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.agents.append(request.headers["User-Agent"])
        items = [
            {"timestamp": f"{(attention.FIRST + timedelta(days=i)):%Y%m%d}00", "views": 1000 + i}
            for i in range(self.days)
        ]
        return httpx.Response(200, json={"items": items})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def test_fetching_identifies_itself_and_reports_problems(tmp_path: Path) -> None:
    fake = FakeWikimedia()
    frame = attention.fetch(
        "Bitcoin", attention.FIRST, attention.FIRST + timedelta(days=59), fake.client()
    )
    assert frame.height == 60
    # Wikimedia asks clients to name themselves: a descriptive client name, and no
    # personal details or accounts in requests to outside services (Luke's rule).
    assert fake.agents[0] == "kestrel-research/0.1"
    attention.save(frame, tmp_path, "btcusdt")
    assert attention.load(tmp_path, "BTCUSDT").equals(frame)
    assert attention.gaps(frame.filter(pl.col("date") != attention.FIRST + timedelta(days=5))) == [
        attention.FIRST + timedelta(days=5)
    ]
    assert attention.gaps(frame.head(1)) == []
    with pytest.raises(attention.AttentionError, match="ingest-attention"):
        attention.load(tmp_path, "ETHUSDT")
    with pytest.raises(attention.AttentionError, match="no Wikipedia article"):
        attention.ingest("DOGEUSDT", fake.client(), tmp_path)
    twice = FakeWikimedia(2)
    original = twice.handler

    def doubled(request: httpx.Request) -> httpx.Response:
        body = original(request).json()
        return httpx.Response(200, json={"items": body["items"] * 2})

    with pytest.raises(attention.AttentionError, match="duplicate"):
        attention.fetch("Bitcoin", D0, D0, httpx.Client(transport=httpx.MockTransport(doubled)))


def test_attention_uses_only_counts_published_by_the_close() -> None:
    bars = daily([0.0] * 20)  # 2024-01-01 to 2024-01-21
    base = [100] * 70
    t = 10  # decide at the close of 2024-01-11
    day_t = (D0 + timedelta(days=t) - (D0 - timedelta(days=40))).days  # index of day t in views
    spike_today = base.copy()
    spike_today[day_t] = 10_000  # published only after the close: must not count
    spike_yesterday = base.copy()
    spike_yesterday[day_t - 1] = 10_000  # published the day before the close: counts
    assert attention_flags(bars, views(spike_today))[t] is False
    assert attention_flags(bars, views(spike_yesterday))[t] is True
    assert attention_flags(bars, views([100] * 10))[t] is None  # not enough history


def test_extreme_fear_is_below_25() -> None:
    bars = daily([0.0] * 3).with_columns(pl.Series("fear_greed", [10, 25, None, 80]))
    assert fear_flags(bars) == [True, False, None, False]


def test_samples_are_sunday_to_sunday() -> None:
    bars = daily([0.01] * 30)  # Sundays are days 6, 13, 20, 27
    flags: list[bool | None] = [True] * 31
    flags[13] = None
    found = sunday_samples(bars, flags, "X")
    assert [s.day.day for s in found] == [7, 21]  # 2024-01-07 and -21; -14 unknown, -28 too late
    assert found[0].forward == pytest.approx(0.07)
    gappy = bars.filter(pl.col("open_time") != datetime(2024, 1, 10, tzinfo=UTC))
    # The 10th is missing: the week from the 7th is skipped, the week from the 14th is whole.
    assert [s.day.day for s in sunday_samples(gappy, [True] * gappy.height, "X")] == [14, 21]


def test_momentum_overlap_and_the_verdict() -> None:
    bars = daily([0.01] * 6 + [-0.01] * 7 + [0.02] * 30)
    found = sunday_samples(bars, [True] * 44, "X")
    overlap = momentum_overlap(bars, found)
    assert overlap.rose_when_on is not None
    assert overlap.rose_when_off is None
    good = [Sample("X", datetime(2024, 1, 7, tzinfo=UTC), True, 0.02)] * 40
    bad = [Sample("X", datetime(2024, 1, 7, tzinfo=UTC), False, -0.01)] * 40
    proceed = SignalResult(
        "attention", ["X"], math.log1p(0.0022), compare(good + bad, 200, 0), {"X": overlap}
    )
    assert proceed.verdict == "PROCEED"
    assert "PROCEED" in format_signal_study(proceed)
    flat = SignalResult("attention", ["X"], 0.5, compare(good + bad, 200, 0), {})
    assert flat.verdict == "KILL"  # confident, but not past a 50% hurdle
    one_sided = SignalResult("attention", ["X"], 0.0, compare(good, 200, 0), {})
    assert one_sided.verdict == "KILL"
    with pytest.raises(ValueError, match="unknown signal"):
        run_signal_study("twitter", {}, hurdle=0.0)


def test_the_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import kestrel.cli as cli

    client = FakeWikimedia(120).client()
    monkeypatch.setattr(cli.httpx, "Client", lambda **_: client)
    ok = CliRunner().invoke(app, ["ingest-attention", "btcusdt", "--data-dir", str(tmp_path)])
    assert ok.exit_code == 0, ok.output
    assert "BTCUSDT: 120 days" in ok.output
    bad = CliRunner().invoke(app, ["ingest-attention", "DOGEUSDT", "--data-dir", str(tmp_path)])
    assert bad.exit_code == 1

    start = datetime(2025, 3, 1, tzinfo=UTC)
    bars = daily([0.01 * (1 if i % 14 < 7 else -1) for i in range(300)])
    bars = bars.with_columns(pl.col("open_time") - (bars["open_time"][0] - start))
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, bars)
    attention.save(
        views([100 + (i % 9) * 20 for i in range(400)], start.date() - timedelta(days=60)),
        tmp_path,
        "BTCUSDT",
    )
    common = [
        "--data-dir",
        str(tmp_path),
        "--config",
        REPO_CONFIG,
        "--research-dir",
        str(tmp_path / "r"),
    ]
    study = CliRunner().invoke(app, ["signal-study", "attention", "BTCUSDT", *common])
    assert study.exit_code == 0, study.output
    assert "Signal study  ·  attention  ·  BTCUSDT" in study.output
    assert "Past week rose" in study.output
    unknown = CliRunner().invoke(app, ["signal-study", "twitter", "BTCUSDT", *common])
    assert unknown.exit_code == 1
    missing = CliRunner().invoke(app, ["signal-study", "attention", "ETHUSDT", *common])
    assert "no stored ETHUSDT 1d bars" in missing.output
