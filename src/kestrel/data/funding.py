"""Perpetual futures funding rates: how crowded and leveraged the market is.

Perpetual futures never expire, so the exchange keeps their price near spot with a payment
between longs and shorts at every settlement (every 8 hours for BTC and ETH). When most
traders are long with leverage, the rate rises and longs pay shorts; when the crowd is
short, it turns negative. With no premium the rate sits at Binance's default interest
component, 0.01% per 8 hours (about 11% a year).

Kestrel trades spot only. Funding is read as information, never traded: Binance's
perpetuals aren't available to Australian retail, but its published history is.

Source: Binance's bulk archive (data.binance.vision, futures/um/monthly/fundingRate), one
zipped CSV per month with a SHA-256 checksum, from 2020-01. Each row is one settlement:
its time, the funding interval in hours, and the rate paid. A settlement's rate is treated
as known at its settlement time (the archive stamps it up to a few milliseconds after the
hour, and that stamp is used as is). Gaps between settlements are reported when the
history is fetched, never filled.

The feature attached to bars, ``funding_7d``, is the average rate of the settlements in
the 7 days up to a bar's close, annualised (a rate per interval x settlements per year).
It is null when that window starts before the stored history does, or when the newest
settlement in it is more than ``MAX_AGE`` before the close.
"""

from __future__ import annotations

import io
import itertools
import json
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

from kestrel.atomic import write_atomically
from kestrel.data.binance_vision import Month, month_range, verify_checksum

BASE_URL = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
FEATURE = "funding_7d"
WINDOW = timedelta(days=7)
MAX_AGE = timedelta(days=1)
"""Newest settlement older than this at a bar's close: the feature is unknown."""
SLACK = timedelta(minutes=1)
"""Settlements further apart than their interval plus this are a gap."""
HOURS_PER_YEAR = 365 * 24

SCHEMA = {"time": pl.Datetime("us", "UTC"), "interval_hours": pl.Int16, "rate": pl.Float64}


class FundingError(ValueError):
    """A published funding archive is malformed: reported, never repaired."""


@dataclass(frozen=True)
class FundingHistory:
    symbol: str
    frame: pl.DataFrame
    """One row per settlement, oldest first: time, interval_hours, rate (per interval)."""
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    """(last settlement before, first settlement after) for every hole in the series."""
    not_found: list[Month] = field(default_factory=list)
    """Months Binance publishes no file for."""


def archive_url(symbol: str, month: Month) -> str:
    sym = symbol.upper()
    return f"{BASE_URL}/{sym}/{sym}-fundingRate-{month}.zip"


def parse_archive(payload: bytes) -> pl.DataFrame:
    """One month's settlements, in the stored schema."""
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise FundingError(f"expected exactly one CSV in the archive, found {names}")
        raw = zf.read(names[0])
    try:
        frame = pl.read_csv(
            raw,
            schema_overrides={"calc_time": pl.Int64, "last_funding_rate": pl.Float64},
        )
        frame = frame.select(
            time=pl.from_epoch("calc_time", time_unit="ms").dt.replace_time_zone("UTC"),
            interval_hours=pl.col("funding_interval_hours").cast(pl.Int16),
            rate=pl.col("last_funding_rate"),
        )
    except (pl.exceptions.PolarsError, KeyError) as e:
        raise FundingError(f"malformed funding archive: {e}") from None
    return frame.cast(SCHEMA)  # type: ignore[arg-type]


def history_from(symbol: str, frames: list[pl.DataFrame], not_found: list[Month]) -> FundingHistory:
    """Combine months, refuse duplicates and bad intervals, and list the gaps."""
    if not frames:
        raise FundingError(f"no funding history published for {symbol.upper()} in that range")
    frame = pl.concat(frames).sort("time")
    if frame["time"].is_duplicated().any():
        when = frame.filter(pl.col("time").is_duplicated())["time"][0]
        raise FundingError(f"two settlements stamped {when}")
    if (frame["interval_hours"] <= 0).any():
        raise FundingError("a settlement has a funding interval of zero hours or less")
    rows = frame.select("time", "interval_hours").rows()
    gaps = [
        (a, b)
        for (a, hours), (b, _) in itertools.pairwise(rows)
        if b - a > timedelta(hours=hours) + SLACK
    ]
    return FundingHistory(symbol.upper(), frame, gaps, not_found)


Progress = Callable[[Month, int, int], None]


def fetch(
    symbol: str,
    start: Month,
    end: Month,
    http: httpx.Client | None = None,
    on_month: Progress | None = None,
) -> FundingHistory:
    """Every published month in range, checksums verified. HTTP is injectable so tests
    run offline."""
    client = http or httpx.Client(timeout=60, follow_redirects=True)
    frames, not_found = [], []
    months = list(month_range(start, end))
    for i, month in enumerate(months):
        if on_month is not None:
            on_month(month, i, len(months))
        url = archive_url(symbol, month)
        response = client.get(url)
        if response.status_code == 404:
            not_found.append(month)
            continue
        response.raise_for_status()
        checksum = client.get(url + ".CHECKSUM")
        checksum.raise_for_status()
        verify_checksum(response.content, checksum.text)
        frames.append(parse_archive(response.content))
    return history_from(symbol, frames, not_found)


def path(data_dir: Path | str, symbol: str) -> Path:
    return Path(data_dir) / "funding" / f"{symbol.upper()}.parquet"


def save(history: FundingHistory, data_dir: Path | str) -> Path:
    """Replace the stored history (atomically), with a note of when it was fetched."""
    target = path(data_dir, history.symbol)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(target, lambda tmp: history.frame.write_parquet(tmp))
    meta = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": BASE_URL,
        "first": history.frame["time"][0].isoformat(),
        "last": history.frame["time"][-1].isoformat(),
        "settlements": history.frame.height,
        "gaps": [[a.isoformat(), b.isoformat()] for a, b in history.gaps],
        "not_found": [str(m) for m in history.not_found],
    }
    target.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    return target


def load(data_dir: Path | str, symbol: str) -> pl.DataFrame:
    stored = path(data_dir, symbol)
    if not stored.exists():
        raise ValueError(
            f"no funding history stored for {symbol.upper()}; "
            f"run kestrel ingest-funding {symbol.upper()} first"
        )
    return pl.read_parquet(stored)


def attach(bars: pl.DataFrame, history: pl.DataFrame, bar_length: timedelta) -> pl.DataFrame:
    """Add ``funding_7d``: the annualised average rate settled in the 7 days up to each
    bar's close, from settlements known by then."""
    settled = history.sort("time").select(
        "time",
        total=(pl.col("rate") * HOURS_PER_YEAR / pl.col("interval_hours")).cum_sum(),
        count=pl.int_range(1, pl.len() + 1, dtype=pl.Int64),
    )
    first = history["time"].min()
    # Cumulative sums at the close and at the window's start: their difference covers
    # settlements in (close - 7 days, close].
    with_close = bars.with_columns(
        _close=pl.col("open_time") + bar_length,
        _start=pl.col("open_time") + bar_length - WINDOW,
    )
    at_close = with_close.sort("_close").join_asof(
        settled.rename({"time": "_newest"}), left_on="_close", right_on="_newest"
    )
    both = at_close.sort("_start").join_asof(
        settled.rename({"time": "_before", "total": "_total0", "count": "_count0"}),
        left_on="_start",
        right_on="_before",
    )
    n = pl.col("count") - pl.col("_count0").fill_null(0)
    known = (
        (pl.col("_start") >= first) & (pl.col("_close") - pl.col("_newest") <= MAX_AGE) & (n > 0)
    )
    mean = (pl.col("total") - pl.col("_total0").fill_null(0.0)) / n
    return (
        both.with_columns(pl.when(known.fill_null(False)).then(mean).otherwise(None).alias(FEATURE))
        .drop("_close", "_start", "_newest", "total", "count", "_before", "_total0", "_count0")
        .sort("open_time")
    )
