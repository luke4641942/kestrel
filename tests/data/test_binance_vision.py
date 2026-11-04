from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import polars as pl
import pytest

from kestrel.data.binance_vision import (
    BinanceVisionClient,
    ChecksumMismatchError,
    Month,
    archive_url,
    last_complete_month,
    month_range,
    parse_archive,
    verify_checksum,
)
from kestrel.data.schema import KLINE_SCHEMA, Interval
from tests.conftest import ArchiveFactory, checksum_for


class TestMonth:
    def test_parse_and_format_round_trip(self) -> None:
        assert str(Month.parse("2024-03")) == "2024-03"

    def test_next_rolls_over_year(self) -> None:
        assert Month(2024, 12).next() == Month(2025, 1)

    def test_range_is_inclusive(self) -> None:
        months = list(month_range(Month(2024, 11), Month(2025, 2)))
        assert [str(m) for m in months] == ["2024-11", "2024-12", "2025-01", "2025-02"]

    def test_rejects_invalid_month(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            Month(2024, 13)

    @pytest.mark.parametrize(
        ("today", "expected"),
        [(date(2026, 10, 30), Month(2026, 9)), (date(2026, 1, 5), Month(2025, 12))],
    )
    def test_last_complete_month(self, today: date, expected: Month) -> None:
        assert last_complete_month(today) == expected


def test_archive_url_matches_binance_layout() -> None:
    url = archive_url("btcusdt", Interval.H1, Month(2024, 1))
    assert url == (
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip"
    )


class TestParse:
    def test_produces_canonical_schema(self, make_archive: ArchiveFactory) -> None:
        df = parse_archive(make_archive(n=24))
        assert df.schema == pl.Schema(KLINE_SCHEMA)
        assert df.height == 24

    def test_millisecond_timestamps(self, make_archive: ArchiveFactory) -> None:
        df = parse_archive(make_archive(start=datetime(2024, 1, 1, tzinfo=UTC)))
        assert df["open_time"][0] == datetime(2024, 1, 1, tzinfo=UTC)

    def test_microsecond_timestamps_from_2025(self, make_archive: ArchiveFactory) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        df = parse_archive(make_archive(start=start, microseconds=True))
        assert df["open_time"][0] == start
        assert df["open_time"][1] == datetime(2025, 1, 1, 1, tzinfo=UTC)

    def test_ms_and_us_produce_identical_frames(self, make_archive: ArchiveFactory) -> None:
        start = datetime(2024, 6, 1, tzinfo=UTC)
        ms = parse_archive(make_archive(start=start))
        us = parse_archive(make_archive(start=start, microseconds=True))
        assert ms.equals(us)

    def test_accepts_header_row(self, make_archive: ArchiveFactory) -> None:
        assert parse_archive(make_archive(header=True)).equals(parse_archive(make_archive()))


class TestChecksum:
    def test_valid(self) -> None:
        verify_checksum(b"data", checksum_for(b"data", "x.zip"))

    def test_mismatch_raises(self) -> None:
        with pytest.raises(ChecksumMismatchError):
            verify_checksum(b"data", checksum_for(b"tampered", "x.zip"))


def _client(routes: dict[str, httpx.Response]) -> BinanceVisionClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return BinanceVisionClient(httpx.Client(transport=httpx.MockTransport(handler)))


class TestClient:
    def test_fetch_month_verifies_and_parses(self, make_archive: ArchiveFactory) -> None:
        payload = make_archive()
        url = archive_url("BTCUSDT", Interval.H1, Month(2024, 1))
        client = _client(
            {
                url: httpx.Response(200, content=payload),
                url + ".CHECKSUM": httpx.Response(200, text=checksum_for(payload, "f.zip")),
            }
        )
        df = client.fetch_month("BTCUSDT", Interval.H1, Month(2024, 1))
        assert df is not None
        assert df.height == 24

    def test_corrupted_download_is_rejected(self, make_archive: ArchiveFactory) -> None:
        url = archive_url("BTCUSDT", Interval.H1, Month(2024, 1))
        client = _client(
            {
                url: httpx.Response(200, content=make_archive()),
                url + ".CHECKSUM": httpx.Response(200, text=checksum_for(b"other", "f.zip")),
            }
        )
        with pytest.raises(ChecksumMismatchError):
            client.fetch_month("BTCUSDT", Interval.H1, Month(2024, 1))

    def test_range_reports_unpublished_months(self, make_archive: ArchiveFactory) -> None:
        payload = make_archive()
        url = archive_url("BTCUSDT", Interval.H1, Month(2024, 1))
        client = _client(
            {
                url: httpx.Response(200, content=payload),
                url + ".CHECKSUM": httpx.Response(200, text=checksum_for(payload, "f.zip")),
            }
        )
        result = client.fetch_range("BTCUSDT", Interval.H1, Month(2024, 1), Month(2024, 2))
        assert result.fetched == [Month(2024, 1)]
        assert result.not_found == [Month(2024, 2)]

    def test_server_error_propagates(self) -> None:
        url = archive_url("BTCUSDT", Interval.H1, Month(2024, 1))
        client = _client({url: httpx.Response(500)})
        with pytest.raises(httpx.HTTPStatusError):
            client.fetch_month("BTCUSDT", Interval.H1, Month(2024, 1))


@pytest.mark.network
def test_live_download_of_real_month() -> None:
    """Run locally with `pytest -m network` to confirm the real site still parses."""
    with BinanceVisionClient() as client:
        df = client.fetch_month("BTCUSDT", Interval.D1, Month(2024, 1))
    assert df is not None
    assert df.height == 31


def test_archive_with_several_csvs_is_rejected() -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.csv", "1,2\n")
        zf.writestr("b.csv", "1,2\n")
    with pytest.raises(ValueError, match="exactly one CSV"):
        parse_archive(buf.getvalue())
