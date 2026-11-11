"""The coin universe, against a fake archive served offline."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from kestrel.data.binance_vision import BinanceVisionClient, Month, archive_name
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.data.universe import (
    KLINES_PREFIX,
    ArchiveListing,
    ingest_universe,
    manifest_path,
    split_universe,
)
from tests.conftest import ArchiveFactory, checksum_for

D1 = Interval.D1


def test_coins_exclude_leveraged_tokens_and_stablecoins_but_keep_lookalikes() -> None:
    symbols = [
        "BTCUSDT", "BTCUPUSDT", "BTCDOWNUSDT", "BNBBULLUSDT", "BNBUSDT", "BULLUSDT",
        "JUPUSDT", "SYRUPUSDT", "AUDIOUSDT", "AUDUSDT", "USDCUSDT", "PAXGUSDT", "USTCUSDT",
        "LUNAUSDT", "ETHBTC", "USDT",
    ]  # fmt: skip
    coins, excluded = split_universe(symbols)
    assert coins == ["AUDIOUSDT", "BNBUSDT", "BTCUSDT", "JUPUSDT", "LUNAUSDT", "SYRUPUSDT"]
    reasons = {e.symbol: e.reason for e in excluded}
    assert reasons == {
        "BTCUPUSDT": "leveraged token",
        "BTCDOWNUSDT": "leveraged token",
        "BNBBULLUSDT": "leveraged token",
        "BULLUSDT": "leveraged token",  # Binance's 3x Bitcoin token
        "AUDUSDT": "stablecoin, fiat or gold",
        "USDCUSDT": "stablecoin, fiat or gold",
        "PAXGUSDT": "stablecoin, fiat or gold",
        "USTCUSDT": "stablecoin, fiat or gold",
    }


# ---------- a fake archive ----------


def _listing(
    prefixes: Sequence[str] = (), keys: Sequence[str] = (), truncated: bool = False
) -> str:
    body = "".join(f"<CommonPrefixes><Prefix>{p}</Prefix></CommonPrefixes>" for p in prefixes)
    body += "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
    return (
        '<?xml version="1.0"?><ListBucketResult>'
        f"<IsTruncated>{'true' if truncated else 'false'}</IsTruncated>{body}</ListBucketResult>"
    )


class FakeArchive:
    """Serves a bucket listing (in pages of two) and monthly daily-bar archives."""

    def __init__(self, make_archive: ArchiveFactory, coins: dict[str, list[str]]) -> None:
        self.coins = coins
        self.make_archive = make_archive
        self.downloads: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.host.startswith("s3-"):
            prefix = request.url.params["prefix"]
            marker = request.url.params.get("marker", "")
            if prefix == KLINES_PREFIX:
                names = sorted(self.coins)
                after = [n for n in names if f"{KLINES_PREFIX}{n}" > marker.rstrip("/")]
                page = after[:2]
                return httpx.Response(
                    200,
                    text=_listing([f"{KLINES_PREFIX}{n}/" for n in page], truncated=len(after) > 2),
                )
            symbol = prefix.removeprefix(KLINES_PREFIX).split("/")[0]
            keys = [
                f"{prefix}{archive_name(symbol, D1, Month.parse(m))}" for m in self.coins[symbol]
            ]
            return httpx.Response(200, text=_listing(keys=[*keys, f"{prefix}x.zip.CHECKSUM"]))
        name = url.rsplit("/", 1)[1]
        symbol, _, month = name.removesuffix(".CHECKSUM").removesuffix(".zip").split("-", 2)
        m = Month.parse(month)
        payload = self.make_archive(
            start=datetime(m.year, m.month, 1, tzinfo=UTC), n=28, step=timedelta(days=1)
        )
        if url.endswith(".CHECKSUM"):
            return httpx.Response(200, text=checksum_for(payload, name.removesuffix(".CHECKSUM")))
        self.downloads.append(name)
        return httpx.Response(200, content=payload)


@pytest.fixture
def archive(make_archive: ArchiveFactory) -> FakeArchive:
    return FakeArchive(
        make_archive,
        {
            "BTCUSDT": ["2024-01", "2024-02", "2024-03"],
            "LUNAUSDT": ["2024-01", "2024-02"],  # delisted part-way
            "BTCUPUSDT": ["2024-01"],
            "USDCUSDT": ["2024-01"],
            "NEWUSDT": ["2024-03"],
        },
    )


def _clients(archive: FakeArchive) -> tuple[ArchiveListing, BinanceVisionClient]:
    transport = httpx.MockTransport(archive.handler)
    return ArchiveListing(httpx.Client(transport=transport)), BinanceVisionClient(
        httpx.Client(transport=transport)
    )


def test_listing_reads_every_page_and_each_coins_months(archive: FakeArchive) -> None:
    listing, _ = _clients(archive)
    assert listing.symbols() == sorted(archive.coins)  # five symbols over three pages
    assert [str(m) for m in listing.months("LUNAUSDT", D1)] == ["2024-01", "2024-02"]


def test_ingest_stores_delisted_coins_and_writes_a_manifest(
    archive: FakeArchive, tmp_path: Path
) -> None:
    listing, client = _clients(archive)
    store = ParquetStore(tmp_path)
    seen: list[str] = []
    results, excluded = ingest_universe(
        listing, client, store, D1, Month.parse("2024-02"), Month.parse("2024-03"),
        workers=2, on_coin=lambda c, i, n: seen.append(c.symbol),
    )  # fmt: skip
    assert {r.symbol: (r.archived, r.fetched) for r in results} == {
        "BTCUSDT": (2, 2),
        "LUNAUSDT": (1, 1),  # only its months inside the range
        "NEWUSDT": (1, 1),
    }
    assert sorted(seen) == ["BTCUSDT", "LUNAUSDT", "NEWUSDT"]
    assert {e.symbol for e in excluded} == {"BTCUPUSDT", "USDCUSDT"}
    assert store.months("LUNAUSDT", D1) == ["2024-02"]
    manifest = json.loads(manifest_path(store, D1).read_text())
    assert manifest["coins"] == {"BTCUSDT": 2, "LUNAUSDT": 1, "NEWUSDT": 1}
    assert manifest["start"] == "2024-02"
    assert {e["symbol"] for e in manifest["excluded"]} == {"BTCUPUSDT", "USDCUSDT"}


def test_ingest_skips_months_already_stored(archive: FakeArchive, tmp_path: Path) -> None:
    listing, client = _clients(archive)
    store = ParquetStore(tmp_path)
    args = (listing, client, store, D1, Month.parse("2024-01"), Month.parse("2024-03"))
    ingest_universe(*args, workers=1)
    first = len(archive.downloads)
    results, _ = ingest_universe(*args, workers=1)
    assert len(archive.downloads) == first  # nothing downloaded twice
    assert all(r.fetched == 0 for r in results)


def test_the_cli_command(
    archive: FakeArchive, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import kestrel.cli as cli

    # Fresh clients per run: the command closes them when it finishes.
    monkeypatch.setattr(cli, "ArchiveListing", lambda: _clients(archive)[0])
    monkeypatch.setattr(cli, "BinanceVisionClient", lambda: _clients(archive)[1])
    args = [
        "ingest-universe",
        "--start",
        "2024-01",
        "--end",
        "2024-03",
        "--data-dir",
        str(tmp_path),
    ]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert "3 coins with 1d data" in result.output
    assert "2 pairs excluded" in result.output
    # Run again: nothing new, so only the final coin's progress line is printed.
    again = CliRunner().invoke(cli.app, args)
    assert again.exit_code == 0, again.output
    assert "[1/3]" not in again.output
    assert "[3/3]" in again.output
    assert "(0 months fetched now)" in again.output
