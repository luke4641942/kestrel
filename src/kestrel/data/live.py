"""The latest closed bars, from Binance's public REST API, for paper and live trading.

The monthly archive (kestrel.data.binance_vision) is published after each month ends, so a
strategy trading today needs the bars since then from ``GET /api/v3/klines``. Only *closed*
bars are kept: a bar whose close time hasn't passed is still moving, and deciding on it
would be lookahead.

The store writes whole months, so new bars are merged into the month already stored
rather than written on their own (which would replace it). Every update is checked with
the same quality audit as an archive ingest; problems are reported, never repaired. The
archive stays authoritative: ingesting a month later replaces the live bars for it.

HTTP is injected, so tests run offline; the public endpoint needs no key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import polars as pl

from kestrel.data.binance_vision import RAW_COLUMNS
from kestrel.data.quality import QualityReport, check_klines
from kestrel.data.schema import Interval, conform
from kestrel.data.store import ParquetStore

REST_URL = "https://api.binance.com/api/v3/klines"
PAGE = 1000
"""Binance's maximum bars per request."""


def parse_rest(rows: list[list[object]]) -> pl.DataFrame:
    """REST kline arrays (the same twelve fields as the archive CSV) to the canonical schema."""
    if not rows:
        return conform(
            pl.DataFrame(schema={c: pl.Float64 for c in RAW_COLUMNS}).with_columns(
                pl.col("open_time").cast(pl.Datetime("us", "UTC"))
            )
        )
    df = pl.DataFrame(
        [[str(v) for v in row[: len(RAW_COLUMNS)]] for row in rows],
        schema=RAW_COLUMNS,
        orient="row",
    )
    df = df.with_columns(
        pl.from_epoch(pl.col("open_time").cast(pl.Int64), time_unit="ms").dt.replace_time_zone(
            "UTC"
        ),
        pl.col("close_time").cast(pl.Int64),
    )
    return conform(df)


def fetch_closed(
    symbol: str,
    interval: Interval,
    since: datetime,
    now: datetime,
    http: httpx.Client,
) -> pl.DataFrame:
    """Every bar opening at or after ``since`` that has closed by ``now``."""
    frames: list[pl.DataFrame] = []
    start = since
    while start + interval.duration <= now:
        params: dict[str, str | int] = {
            "symbol": symbol.upper(),
            "interval": interval.value,
            "startTime": int(start.timestamp() * 1000),
            "limit": PAGE,
        }
        response = http.get(REST_URL, params=params)
        response.raise_for_status()
        page = parse_rest(response.json())
        if page.is_empty():
            break
        frames.append(page)
        newest: datetime = page["open_time"].max()  # type: ignore[assignment]
        start = newest + interval.duration
        if page.height < PAGE:
            break
    if not frames:
        return parse_rest([])
    bars = pl.concat(frames).unique("open_time", keep="last").sort("open_time")
    # Closed: its whole interval has passed. The bar still forming is never kept.
    return bars.filter(pl.col("open_time") + interval.duration <= now)


@dataclass(frozen=True)
class Update:
    symbol: str
    interval: Interval
    new_bars: int
    last: datetime | None
    """Open time of the newest stored bar after the update."""
    quality: QualityReport
    """The audit of every month the update touched."""


def update(
    store: ParquetStore,
    symbol: str,
    interval: Interval,
    http: httpx.Client,
    now: datetime | None = None,
) -> Update:
    """Bring a stored market up to the last closed bar. It must already have archive data:
    the live feed extends history, it doesn't start it."""
    now = now or datetime.now(UTC)
    symbol = symbol.upper()
    months = store.months(symbol, interval)
    if not months:
        raise ValueError(f"no stored {symbol} {interval.value} bars; ingest the archive first")
    stored = store.read(symbol, interval, datetime.fromisoformat(f"{months[-1]}-01T00:00+00:00"))
    last: datetime = stored["open_time"].max()  # type: ignore[assignment]
    new = fetch_closed(symbol, interval, last + interval.duration, now, http)
    if new.is_empty():
        return Update(symbol, interval, 0, last, check_klines(stored, interval))
    first_new: datetime = new["open_time"].min()  # type: ignore[assignment]
    first_month = datetime(first_new.year, first_new.month, 1, tzinfo=UTC)
    # Whole months: from the start of the last stored month (or the first new one, if
    # earlier), so writing them back keeps every bar already stored.
    stored_from: datetime = stored["open_time"].min()  # type: ignore[assignment]
    existing = store.read(symbol, interval, min(first_month, stored_from))
    merged = pl.concat([existing, new]).unique("open_time", keep="first").sort("open_time")
    store.write(symbol, interval, merged)
    touched = merged.filter(pl.col("open_time") >= first_month)
    return Update(
        symbol,
        interval,
        new.height,
        merged["open_time"].max(),  # type: ignore[arg-type]
        check_klines(touched, interval),
    )


def open_of(symbol: str, interval: Interval, at: datetime, http: httpx.Client) -> Decimal | None:
    """The open price of the bar starting at ``at``, once it has started (it is still
    forming, so only its open is final); None if Binance has no bar there yet."""
    params: dict[str, str | int] = {
        "symbol": symbol.upper(),
        "interval": interval.value,
        "startTime": int(at.timestamp() * 1000),
        "limit": 1,
    }
    response = http.get(REST_URL, params=params)
    response.raise_for_status()
    rows = response.json()
    if not rows or int(rows[0][0]) != int(at.timestamp() * 1000):
        return None
    return Decimal(str(rows[0][1]))
