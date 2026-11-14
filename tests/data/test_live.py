"""The live feed: closed bars only, merged into stored months, audited. Offline, against a
fake of Binance's REST endpoint, plus one network check of the real one."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from kestrel.data import live
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from tests.backtest.helpers import bars

D1 = Interval.D1
DAY = timedelta(days=1)
T0 = datetime(2024, 1, 1, tzinfo=UTC)


def rest_row(open_time: datetime, close: float) -> list[object]:
    ms = int(open_time.timestamp() * 1000)
    return [ms, "100.0", str(close + 1), "99.0", str(close), "10.5", ms + 86_399_999,
            "1050.0", 42, "5.0", "500.0", "0"]  # fmt: skip


class FakeBinance:
    """Serves daily bars from T0 for ``days`` days; records each request's start time."""

    def __init__(self, days: int) -> None:
        self.rows = [rest_row(T0 + i * DAY, 100.0 + i) for i in range(days)]
        self.starts: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startTime"])
        limit = int(request.url.params["limit"])
        self.starts.append(start)
        page = [r for r in self.rows if int(r[0]) >= start][:limit]  # type: ignore[call-overload]
        return httpx.Response(200, json=page)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def test_rest_rows_become_canonical_bars() -> None:
    df = live.parse_rest([rest_row(T0, 123.5)])
    assert df["open_time"].to_list() == [T0]
    assert (df["close"][0], df["high"][0], df["quote_volume"][0]) == (123.5, 124.5, 1050.0)
    assert live.parse_rest([]).is_empty()
    assert live.parse_rest([]).columns == df.columns


def test_only_closed_bars_are_kept_and_pages_are_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "PAGE", 3)
    fake = FakeBinance(days=10)
    # Day 10 (index 9) opens at T0 + 9 days and closes at T0 + 10 days: still forming at
    # T0 + 9.5 days, so it is left out.
    got = live.fetch_closed("btcusdt", D1, T0 + 2 * DAY, T0 + 9.5 * DAY, fake.client())
    assert got["open_time"].to_list() == [T0 + i * DAY for i in range(2, 9)]
    assert len(fake.starts) == 3  # 3 + 3 + 1 bars
    closed = live.fetch_closed("BTCUSDT", D1, T0 + 2 * DAY, T0 + 10 * DAY, fake.client())
    assert closed["open_time"].max() == T0 + 9 * DAY  # at its close time it counts
    nothing = live.fetch_closed("BTCUSDT", D1, T0 + 9 * DAY, T0 + 9.5 * DAY, fake.client())
    assert nothing.is_empty()
    # Binance has nothing from that start yet (the feed is ahead of the exchange): stop.
    ahead = live.fetch_closed("BTCUSDT", D1, T0 + 30 * DAY, T0 + 60 * DAY, fake.client())
    assert ahead.is_empty()


def test_an_update_merges_into_the_stored_month(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    # The archive holds 1-15 January; the live feed has bars to 9 February.
    archive = bars([100.0 + i for i in range(15)], step=DAY)
    store.write("BTCUSDT", D1, archive)
    fake = FakeBinance(days=40)
    now = T0 + 40 * DAY + timedelta(hours=1)
    up = live.update(store, "btcusdt", D1, fake.client(), now=now)
    assert up.new_bars == 25
    assert up.last == T0 + 39 * DAY
    assert up.quality.is_clean
    stored = store.read("BTCUSDT", D1)
    assert stored.height == 40
    # The archive's January bars were kept as they were, not replaced.
    assert stored.head(15)["close"].to_list() == archive["close"].to_list()
    again = live.update(store, "BTCUSDT", D1, fake.client(), now=now)
    assert (again.new_bars, again.last) == (0, T0 + 39 * DAY)


def test_an_update_reports_problems_instead_of_repairing_them(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", D1, bars([100.0] * 5, step=DAY))
    fake = FakeBinance(days=12)
    del fake.rows[8]  # Binance skips a day
    up = live.update(store, "BTCUSDT", D1, fake.client(), now=T0 + 12 * DAY)
    # A gap is reported, not treated as corruption (is_clean covers integrity errors; the
    # archive has real exchange outages). The paper runner refuses to trade on it (P4 S4).
    assert up.quality.missing_bars == 1
    assert up.quality.gaps[0].before == T0 + 9 * DAY


def test_the_feed_extends_history_but_doesnt_start_it(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ingest the archive first"):
        live.update(ParquetStore(tmp_path), "BTCUSDT", D1, FakeBinance(3).client())


@pytest.mark.network
def test_the_real_endpoint() -> None:
    now = datetime.now(UTC)
    with httpx.Client(timeout=20) as http:
        got = live.fetch_closed("BTCUSDT", D1, now - 5 * DAY, now, http)
    assert 3 <= got.height <= 5
    assert (got["open_time"] + DAY <= now).all()


def test_the_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    import kestrel.cli as cli

    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", D1, bars([100.0] * 5, step=DAY))
    client = FakeBinance(days=8).client()  # made before httpx.Client is patched
    monkeypatch.setattr(cli.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(live, "datetime", _Frozen)
    ok = CliRunner().invoke(cli.app, ["update", "btcusdt", "ethusdt", "--data-dir", str(tmp_path)])
    assert "BTCUSDT 1d: 3 new bars, last 2024-01-08 00:00 UTC (clean)" in ok.output
    assert "ETHUSDT: no stored ETHUSDT 1d bars" in ok.output
    assert ok.exit_code == 1  # one market failed


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def,override]
        return datetime(2024, 1, 9, 1, tzinfo=UTC)
