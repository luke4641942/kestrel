"""Open interest of Binance's USDT-M perpetuals, from the public archive (data only: perps
can't be traded from Australia, but how many contracts are open says when leveraged
traders were forced out).

Source: data.binance.vision/data/futures/um/daily/metrics/<PERP>/<PERP>-metrics-<day>.zip,
one CSV a day of 5-minute rows (create_time 00:00 to 23:55 UTC) with ``sum_open_interest``
(contracts) and ``sum_open_interest_value`` (contracts x price). Only contracts are kept:
the value falls whenever price falls, so a signal on it would just be a price signal.

A day's figure is the latest row stamped at or before its 00:00 UTC close, if one falls in
the last hour (note do-falls-with-forced-perp-selling-bounce-more), so only the rows from
23:00 to the next 00:00 are stored. Missing files and days without a row in that hour are
reported, never filled. The zip's own CRC checks each file; there is no separate checksum.
"""

from __future__ import annotations

import io
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

from kestrel.atomic import write_atomically

LIST_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
FILE_URL = (
    "https://data.binance.vision/data/futures/um/daily/metrics/{perp}/{perp}-metrics-{day}.zip"
)
PREFIX = "data/futures/um/daily/metrics/"
USER_AGENT = "kestrel-research/0.1"
LISTED_BY = date(2021, 12, 1)
"""The study's coins: perps whose metrics start on or before this day (its note, v2: the
archive's start for every perp but BTC; v1's 2021-06-30 left none)."""
LAST_HOUR = timedelta(hours=1)
SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "file_date": pl.Date,
    "create_time": pl.Datetime("us", "UTC"),
    "open_interest": pl.Float64,
}
_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_DAY = re.compile(r"-metrics-(\d{4}-\d{2}-\d{2})\.zip$")


def _list(http: httpx.Client, prefix: str, delimiter: str | None = None) -> Iterator[ET.Element]:
    """Every page of an S3 listing under ``prefix``."""
    marker = ""
    while True:
        params = {"prefix": prefix, "marker": marker, "max-keys": "1000"}
        if delimiter:
            params["delimiter"] = delimiter
        res = http.get(LIST_URL, params=params)
        res.raise_for_status()
        root = ET.fromstring(res.content)
        yield root
        if root.findtext(f"{_S3}IsTruncated") != "true":
            return
        marker = root.findtext(f"{_S3}NextMarker") or ""
        if not marker:
            keys = [k.text or "" for k in root.iter(f"{_S3}Key")]
            prefixes = [p.text or "" for p in root.iter(f"{_S3}Prefix")][1:]
            marker = (keys or prefixes or [""])[-1]
            if not marker:
                return


def perps(http: httpx.Client) -> list[str]:
    """Every USDT-M perp with a metrics folder."""
    out: list[str] = []
    for page in _list(http, PREFIX, "/"):
        for cp in page.iter(f"{_S3}CommonPrefixes"):
            name = (cp.findtext(f"{_S3}Prefix") or "").removeprefix(PREFIX).strip("/")
            if name:
                out.append(name)
    return sorted(set(out))


def days(http: httpx.Client, perp: str) -> list[date]:
    """The days the archive holds a metrics file for ``perp``."""
    found: list[date] = []
    for page in _list(http, f"{PREFIX}{perp}/"):
        for key in page.iter(f"{_S3}Key"):
            m = _DAY.search(key.text or "")
            if m:
                found.append(date.fromisoformat(m.group(1)))
    return sorted(set(found))


def first_day(http: httpx.Client, perp: str) -> date | None:
    res = http.get(LIST_URL, params={"prefix": f"{PREFIX}{perp}/", "max-keys": "1"})
    res.raise_for_status()
    for key in ET.fromstring(res.content).iter(f"{_S3}Key"):
        m = _DAY.search(key.text or "")
        if m:
            return date.fromisoformat(m.group(1))
    return None


def spot_symbol(perp: str) -> str:
    """The spot pair a perp tracks: 1000SHIBUSDT is SHIBUSDT (its contract is 1,000 coins;
    the scale cancels in a change of open interest)."""
    return re.sub(r"^(1000000|1000)(?=[A-Z])", "", perp)


def parse_day(payload: bytes, day: date) -> pl.DataFrame:
    """The rows of one day's file that a close can use: from 23:00 to the next 00:00, plus
    the day's own 00:00 row (the previous day's close)."""
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        raw = z.read(name)  # CRC-checked
    frame = pl.read_csv(io.BytesIO(raw), columns=["create_time", "sum_open_interest"])
    rows = frame.select(
        file_date=pl.lit(day, pl.Date),
        create_time=pl.col("create_time")
        .str.to_datetime("%Y-%m-%d %H:%M:%S", time_zone="UTC")
        .dt.cast_time_unit("us"),
        open_interest=pl.col("sum_open_interest").cast(pl.Float64),
    )
    hour, minute = pl.col("create_time").dt.hour(), pl.col("create_time").dt.minute()
    return rows.filter((hour == 23) | ((hour == 0) & (minute == 0)))


def path(data_dir: Path | str, perp: str) -> Path:
    return Path(data_dir) / "open_interest" / f"{perp.upper()}.parquet"


def load(data_dir: Path | str, perp: str) -> pl.DataFrame:
    p = path(data_dir, perp)
    return pl.read_parquet(p) if p.exists() else pl.DataFrame(schema=SCHEMA)


def _save(frame: pl.DataFrame, data_dir: Path | str, perp: str) -> None:
    target = path(data_dir, perp)
    target.parent.mkdir(parents=True, exist_ok=True)
    ordered = frame.unique(["file_date", "create_time"]).sort("create_time", "file_date")
    write_atomically(target, lambda tmp: ordered.write_parquet(tmp))


@dataclass
class Ingested:
    perp: str
    added: int = 0
    stored: int = 0
    failed: list[date] = field(default_factory=list)


def ingest(
    perp: str,
    http: httpx.Client,
    data_dir: Path | str,
    last: date,
    *,
    workers: int = 4,
    save_every: int = 200,
    progress: Callable[[int, int], None] = lambda done, total: None,
) -> Ingested:
    """Download every archived day of ``perp`` up to ``last`` not already stored. Resumes:
    a day already in the store isn't fetched again. A failed file is reported and left for
    the next run."""
    stored = load(data_dir, perp)
    have = set(stored["file_date"].to_list())
    wanted = [d for d in days(http, perp) if d <= last and d not in have]
    out = Ingested(perp)
    frames = [stored]

    def fetch(day: date) -> tuple[date, pl.DataFrame | None]:
        url = FILE_URL.format(perp=perp, day=day.isoformat())
        for attempt in range(3):
            try:
                res = http.get(url)
                if res.status_code == 404:
                    return day, None
                res.raise_for_status()
                return day, parse_day(res.content, day)
            except (httpx.HTTPError, zipfile.BadZipFile, KeyError, StopIteration):
                time.sleep(2 * (attempt + 1))
        return day, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (day, frame) in enumerate(pool.map(fetch, wanted), start=1):
            if frame is None:
                out.failed.append(day)
            else:
                frames.append(frame)
                out.added += 1
            if i % save_every == 0:
                _save(pl.concat(frames), data_dir, perp)
                frames = [load(data_dir, perp)]
            progress(i, len(wanted))
    merged = pl.concat(frames)
    _save(merged, data_dir, perp)
    out.stored = load(data_dir, perp)["file_date"].n_unique()
    return out


def closes(stored: pl.DataFrame) -> pl.DataFrame:
    """Each day's open interest at its 00:00 UTC close: the latest row stamped at or before
    the close, if it falls in the last hour; days without one are absent (never filled)."""
    if stored.is_empty():
        return pl.DataFrame(schema={"date": pl.Date, "open_interest": pl.Float64})
    rows = stored.unique("create_time").with_columns(
        date=(pl.col("create_time") - pl.duration(microseconds=1)).dt.date()
    )
    close = pl.col("date").cast(pl.Datetime("us", "UTC")) + pl.duration(days=1)
    in_hour = (pl.col("create_time") <= close) & (pl.col("create_time") > close - LAST_HOUR)
    return (
        rows.filter(in_hour)
        .sort("create_time")
        .group_by("date", maintain_order=True)
        .agg(pl.col("open_interest").last())
        .sort("date")
    )


def universe(
    http: httpx.Client, spot: set[str], locked: set[str], listed_by: date = LISTED_BY
) -> tuple[dict[str, str], list[str]]:
    """The study's coins: perp -> spot pair, for every perp whose metrics start on or before
    ``listed_by`` and whose spot pair is stored, outside the locked half. Also the perps left
    out, each with why."""
    chosen: dict[str, str] = {}
    left: list[str] = []
    for perp in perps(http):
        coin = spot_symbol(perp)
        if coin in locked:
            left.append(f"{perp}: locked half")
            continue
        if coin not in spot:
            left.append(f"{perp}: no stored spot pair")
            continue
        first = first_day(http, perp)
        if first is None or first > listed_by:
            left.append(f"{perp}: metrics from {first}")
            continue
        if coin in chosen.values():
            left.append(f"{perp}: {coin} already has a perp")
            continue
        chosen[perp] = coin
    return chosen, left


def client() -> httpx.Client:
    return httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)
