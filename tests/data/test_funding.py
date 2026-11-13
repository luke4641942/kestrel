"""Funding rates: parsing Binance's archive, storage, and when each settlement is known."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.data import funding
from kestrel.data.binance_vision import ChecksumMismatchError, Month
from kestrel.data.features import attach_features
from kestrel.data.schema import Interval
from tests.backtest.helpers import bars

T0 = datetime(2024, 1, 1, tzinfo=UTC)
EIGHT_HOURS = timedelta(hours=8)


def archive(rows: list[tuple[datetime, int, float]], header: bool = True) -> bytes:
    lines = ["calc_time,funding_interval_hours,last_funding_rate"] if header else []
    lines += [f"{int(t.timestamp() * 1000)},{h},{r}" for t, h, r in rows]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("BTCUSDT-fundingRate.csv", "\n".join(lines) + "\n")
    return buffer.getvalue()


def settlements(*rates: float, start: datetime = T0) -> list[tuple[datetime, int, float]]:
    return [(start + i * EIGHT_HOURS, 8, r) for i, r in enumerate(rates)]


def history(*rates: float, start: datetime = T0) -> pl.DataFrame:
    return funding.parse_archive(archive(settlements(*rates, start=start)))


def test_parse_reads_times_intervals_and_rates() -> None:
    rows = settlements(0.0001, -0.0002)
    rows[1] = (rows[1][0] + timedelta(milliseconds=2), 8, -0.0002)  # Binance stamps a little late
    frame = funding.parse_archive(archive(rows))
    assert frame.schema == pl.Schema(funding.SCHEMA)
    assert frame["time"].to_list() == [T0, T0 + EIGHT_HOURS + timedelta(milliseconds=2)]
    assert frame["rate"].to_list() == [0.0001, -0.0002]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not a zip", None),
        (archive(settlements(0.0001), header=False), "malformed"),
    ],
)
def test_malformed_archives_are_refused(payload: bytes, message: str | None) -> None:
    with pytest.raises((funding.FundingError, zipfile.BadZipFile), match=message):
        funding.parse_archive(payload)


def test_an_archive_needs_exactly_one_csv() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("a.txt", "x")
    with pytest.raises(funding.FundingError, match="exactly one CSV"):
        funding.parse_archive(buffer.getvalue())


def test_gaps_are_reported_and_duplicates_refused() -> None:
    rows = settlements(0.0001, 0.0001, 0.0001, 0.0001)
    del rows[2]  # one settlement missing
    h = funding.history_from("btcusdt", [funding.parse_archive(archive(rows))], [])
    assert h.symbol == "BTCUSDT"
    assert h.gaps == [(T0 + EIGHT_HOURS, T0 + 3 * EIGHT_HOURS)]
    with pytest.raises(funding.FundingError, match="two settlements"):
        funding.history_from("BTCUSDT", [history(0.0001), history(0.0001)], [])
    with pytest.raises(funding.FundingError, match="no funding history"):
        funding.history_from("BTCUSDT", [], [Month(2024, 1)])
    with pytest.raises(funding.FundingError, match="zero hours"):
        funding.history_from("BTCUSDT", [funding.parse_archive(archive([(T0, 0, 0.0)]))], [])


def mock_archive(months: dict[str, bytes], bad_checksum: bool = False) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for month, payload in months.items():
            name = f"BTCUSDT-fundingRate-{month}.zip"
            if url.endswith(name):
                return httpx.Response(200, content=payload)
            if url.endswith(name + ".CHECKSUM"):
                digest = "0" * 64 if bad_checksum else hashlib.sha256(payload).hexdigest()
                return httpx.Response(200, text=f"{digest}  {name}")
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_verifies_checksums_and_lists_missing_months() -> None:
    feb = T0.replace(month=2)
    months = {
        "2024-01": archive(settlements(0.0001)),
        "2024-02": archive(settlements(0.0002, start=feb)),
    }
    seen: list[Month] = []
    h = funding.fetch(
        "BTCUSDT",
        Month(2023, 12),
        Month(2024, 2),
        mock_archive(months),
        on_month=lambda m, i, n: seen.append(m),
    )
    assert h.frame["rate"].to_list() == [0.0001, 0.0002]
    assert h.not_found == [Month(2023, 12)]
    assert seen == [Month(2023, 12), Month(2024, 1), Month(2024, 2)]
    with pytest.raises(ChecksumMismatchError):
        funding.fetch("BTCUSDT", Month(2024, 1), Month(2024, 1), mock_archive(months, True))


def test_save_and_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ingest-funding ETHUSDT"):
        funding.load(tmp_path, "ethusdt")
    h = funding.history_from("BTCUSDT", [history(0.0001, 0.0002)], [Month(2023, 12)])
    stored = funding.save(h, tmp_path)
    assert funding.load(tmp_path, "BTCUSDT").equals(h.frame)
    assert '"not_found": [\n    "2023-12"' in stored.with_suffix(".json").read_text()


# ---------- when each settlement is known ----------


def daily_bars(n: int, start: datetime = T0) -> pl.DataFrame:
    return bars([100.0] * n, start=start, step=timedelta(days=1))


def annual(rate_per_8h: float) -> float:
    return rate_per_8h * 3 * 365


def test_the_feature_averages_the_last_seven_days_annualised() -> None:
    # 10 days of settlements at 0.01% each, then 0.04%; the window is (close - 7d, close].
    rates = [0.0001] * 30 + [0.0004] * 4
    out = funding.attach(daily_bars(11), history(*rates), timedelta(days=1))
    f = out["funding_7d"].to_list()
    assert f[:6] == [None] * 6  # windows that start before the history does
    assert f[6] == pytest.approx(annual(0.0001))  # bar 6's window starts at the first one
    # Bar 10 closes at day 11 00:00: (day 4, day 11] holds 21 settlements, 4 at 0.04%.
    assert f[10] == pytest.approx(annual((17 * 0.0001 + 4 * 0.0004) / 21))


def test_a_settlement_stamped_after_the_close_waits_for_the_next_bar() -> None:
    rows = settlements(*([0.0] * 33), start=T0 - timedelta(days=10))  # up to T0 16:00
    day1 = T0 + timedelta(days=1)
    rows.append((day1 + timedelta(milliseconds=3), 8, 0.0021))  # just after bar 0 closes
    rows += [(day1 + timedelta(hours=h), 8, 0.0) for h in (8, 16, 24)]
    out = funding.attach(daily_bars(2), funding.parse_archive(archive(rows)), timedelta(days=1))
    f = out["funding_7d"].to_list()
    assert f[0] == pytest.approx(0.0)
    assert f[1] == pytest.approx(annual(0.0021 / 21))  # (day -5, day 2]: 21 settlements


def test_a_four_hour_interval_is_annualised_as_such() -> None:
    rows = [(T0 + i * timedelta(hours=4), 4, 0.0001) for i in range(60)]
    out = funding.attach(
        daily_bars(1, start=T0 + timedelta(days=8)),
        funding.parse_archive(archive(rows)),
        timedelta(days=1),
    )
    assert out["funding_7d"][0] == pytest.approx(0.0001 * 6 * 365)


def test_a_stale_history_is_unknown_not_carried_forward() -> None:
    out = funding.attach(daily_bars(12), history(*([0.0001] * 24)), timedelta(days=1))
    f = out["funding_7d"].to_list()
    # The last settlement is at day 7, 16:00. Bar 7 closes at day 8 00:00 and sees it;
    # bar 8 closes at day 9 00:00, more than a day later.
    assert f[7] is not None
    assert f[8:] == [None] * 4


def test_attach_features_reads_each_coins_own_history(tmp_path: Path) -> None:
    btc = funding.history_from("BTCUSDT", [history(*([0.0001] * 30))], [])
    funding.save(btc, tmp_path)
    eth = funding.history_from("ETHUSDT", [history(*([0.0003] * 30))], [])
    funding.save(eth, tmp_path)
    for symbol, rate in (("BTCUSDT", 0.0001), ("ETHUSDT", 0.0003)):
        out = attach_features(daily_bars(9), ("funding_7d",), symbol, Interval.D1, tmp_path)
        assert out["funding_7d"][8] == pytest.approx(annual(rate))


# ---------- the command line ----------


def test_the_cli_stores_each_symbol_and_reports_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import kestrel.cli as cli

    rows = settlements(0.0001, 0.0001, 0.0001)
    del rows[1]

    def fake(symbol: str, start: Month, end: Month) -> funding.FundingHistory:
        if symbol == "NOPEUSDT":
            raise httpx.ConnectError("offline")
        frame = funding.parse_archive(archive(rows))
        return funding.history_from(symbol, [frame], [Month(2023, 12)])

    monkeypatch.setattr(cli.funding, "fetch", fake)
    args = ["ingest-funding", "BTCUSDT", "NOPEUSDT", "--data-dir", str(tmp_path)]
    result = CliRunner().invoke(cli.app, [*args, "--end", "2024-01"])
    assert result.exit_code == 1
    assert "2 BTCUSDT settlements, 2024-01-01 to 2024-01-01" in result.output
    assert "gap: no settlement between 2024-01-01 00:00 and 16:00 2024-01-01" in result.output
    assert "not published by Binance: 2023-12" in result.output
    assert "couldn't fetch NOPEUSDT funding" in result.output
    assert funding.path(tmp_path, "BTCUSDT").exists()
