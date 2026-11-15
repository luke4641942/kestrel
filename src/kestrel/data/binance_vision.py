"""Ingest historical spot klines from Binance's public bulk-data site.

Source: https://data.binance.vision (monthly zipped CSVs, each with a SHA-256
checksum file). This is the deepest free, official history Binance provides and
avoids REST rate limits entirely.

Format quirks handled here, so nothing downstream has to know about them:
- Files have no header row (some newer files do; both are accepted).
- From 2025-01-01, spot timestamps switched from milliseconds to microseconds.
  Units are detected per row from magnitude, so mixed ranges are safe.
- ``close_time`` is redundant with ``open_time + interval`` and is dropped.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date

import httpx
import polars as pl

from kestrel.data.schema import Interval, conform

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
UM_KLINES_URL = "https://data.binance.vision/data/futures/um/monthly/klines"
"""USDT-M perpetual futures klines: the same file layout and columns as spot."""

RAW_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]

# Epoch values above this are microseconds; below are milliseconds.
# (Milliseconds would not reach 1e14 until the year 5138.)
_US_THRESHOLD = 10**14


class ChecksumMismatchError(Exception):
    """The downloaded archive does not match Binance's published SHA-256."""


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"month out of range: {self.month}")

    @classmethod
    def parse(cls, text: str) -> Month:
        """Parse ``YYYY-MM``."""
        year, month = text.split("-")
        return cls(int(year), int(month))

    def next(self) -> Month:
        return Month(self.year + self.month // 12, self.month % 12 + 1)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def month_range(start: Month, end: Month) -> Iterator[Month]:
    """Inclusive range of months."""
    current = start
    while current <= end:
        yield current
        current = current.next()


def last_complete_month(today: date) -> Month:
    """Binance publishes a monthly file only after the month closes."""
    first = Month(today.year, today.month)
    return Month(first.year - 1, 12) if first.month == 1 else Month(first.year, first.month - 1)


def archive_name(symbol: str, interval: Interval, month: Month) -> str:
    return f"{symbol.upper()}-{interval.value}-{month}.zip"


def archive_url(symbol: str, interval: Interval, month: Month, base: str = BASE_URL) -> str:
    sym = symbol.upper()
    return f"{base}/{sym}/{interval.value}/{archive_name(sym, interval, month)}"


def verify_checksum(payload: bytes, checksum_text: str) -> None:
    """Checksum files look like ``<sha256-hex>  <filename>``."""
    expected = checksum_text.split()[0].strip().lower()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise ChecksumMismatchError(f"expected {expected}, got {actual}")


def parse_archive(payload: bytes) -> pl.DataFrame:
    """Parse one Binance kline zip into the canonical schema."""
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"expected exactly one CSV in archive, found {csv_names}")
        raw = zf.read(csv_names[0])

    has_header = raw[:1].isalpha()
    df = pl.read_csv(
        raw,
        has_header=False,
        skip_rows=1 if has_header else 0,
        new_columns=RAW_COLUMNS,
        schema_overrides={"open_time": pl.Int64, "close_time": pl.Int64},
    )

    epoch_us = (
        pl.when(pl.col("open_time") >= _US_THRESHOLD)
        .then(pl.col("open_time"))
        .otherwise(pl.col("open_time") * 1000)
    )
    df = df.with_columns(pl.from_epoch(epoch_us, time_unit="us").dt.replace_time_zone("UTC"))
    return conform(df)


ProgressCallback = Callable[[Month, int, int], None]


@dataclass
class IngestResult:
    frames: list[pl.DataFrame] = field(default_factory=list)
    fetched: list[Month] = field(default_factory=list)
    not_found: list[Month] = field(default_factory=list)


class BinanceVisionClient:
    """Downloads and verifies monthly kline archives: spot by default, or USDT-M perpetuals
    with ``base=UM_KLINES_URL``.

    The HTTP client is injectable so tests run offline against a mock transport.
    """

    def __init__(
        self, http: httpx.Client | None = None, timeout: float = 60.0, base: str = BASE_URL
    ) -> None:
        self._http = http or httpx.Client(timeout=timeout, follow_redirects=True)
        self._base = base

    def fetch_month(self, symbol: str, interval: Interval, month: Month) -> pl.DataFrame | None:
        """Return the month's bars, or ``None`` if Binance has no file for it."""
        url = archive_url(symbol, interval, month, self._base)
        response = self._http.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()

        checksum = self._http.get(url + ".CHECKSUM")
        checksum.raise_for_status()
        verify_checksum(response.content, checksum.text)
        return parse_archive(response.content)

    def fetch_range(
        self,
        symbol: str,
        interval: Interval,
        start: Month,
        end: Month,
        on_month: ProgressCallback | None = None,
    ) -> IngestResult:
        """Fetch every month in range. ``on_month(month, done, total)`` fires before each."""
        result = IngestResult()
        months = list(month_range(start, end))
        for i, month in enumerate(months):
            if on_month is not None:
                on_month(month, i, len(months))
            frame = self.fetch_month(symbol, interval, month)
            if frame is None:
                result.not_found.append(month)
            else:
                result.frames.append(frame)
                result.fetched.append(month)
        return result

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BinanceVisionClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
