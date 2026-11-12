"""The Crypto Fear & Greed Index: a daily crowd-sentiment score, 0 (extreme fear) to 100
(extreme greed), published by alternative.me since 2018-02-01.

It is information that isn't in the price chart, which is why it is worth testing: every
price-only idea tried so far either failed or turned out to be trend following.

When a value is known matters more than anything else here. Each value is stamped at 00:00
UTC of its day, and may be computed from that whole day, so a value stamped day D is
treated as known only from the end of day D (D + 1 day, 00:00 UTC). A bar may use the
latest value known by its close. Values more than ``MAX_AGE`` older than that are treated
as unknown (null), so a gap in the series never silently carries an old reading forward.

Gaps are reported when the history is fetched, never filled.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

URL = "https://api.alternative.me/fng/"
FEATURE = "fear_greed"
KNOWN_AFTER = timedelta(days=1)
"""A value stamped day D is known from D + 1 day, 00:00 UTC."""
MAX_AGE = timedelta(days=3)
"""Older than this at a bar's close, a value counts as unknown."""

SCHEMA = {"date": pl.Date, "value": pl.Int16, "classification": pl.Utf8}


class SentimentError(ValueError):
    """The published history is malformed: reported, never repaired."""


@dataclass(frozen=True)
class SentimentHistory:
    frame: pl.DataFrame
    """One row per published day, oldest first: date, value, classification."""
    gaps: list[tuple[date, date]]
    """(first missing day, last missing day) for every hole in the series."""


def parse(payload: dict[str, object]) -> SentimentHistory:
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise SentimentError("the Fear & Greed response has no data")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise SentimentError(f"malformed row {row!r}")
        stamp = datetime.fromtimestamp(int(row["timestamp"]), UTC)
        if (stamp.hour, stamp.minute, stamp.second) != (0, 0, 0):
            raise SentimentError(f"value stamped {stamp}, not at 00:00 UTC")
        value = int(row["value"])
        if not 0 <= value <= 100:
            raise SentimentError(f"value {value} on {stamp.date()} is outside 0-100")
        out.append(
            {
                "date": stamp.date(),
                "value": value,
                "classification": str(row["value_classification"]),
            }
        )
    frame = pl.DataFrame(out, schema=SCHEMA).sort("date")
    if frame["date"].is_duplicated().any():
        day = frame.filter(pl.col("date").is_duplicated())["date"][0]
        raise SentimentError(f"two values published for {day}")
    days = frame["date"].to_list()
    gaps = [
        (a + timedelta(days=1), b - timedelta(days=1))
        for a, b in itertools.pairwise(days)
        if b - a > timedelta(days=1)
    ]
    return SentimentHistory(frame, gaps)


def fetch(http: httpx.Client | None = None) -> SentimentHistory:
    """The full published history. HTTP is injectable so tests run offline."""
    client = http or httpx.Client(timeout=60, follow_redirects=True)
    response = client.get(URL, params={"limit": 0, "format": "json"})
    response.raise_for_status()
    return parse(response.json())


def path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "sentiment" / "fear_greed.parquet"


def save(history: SentimentHistory, data_dir: Path | str) -> Path:
    """Replace the stored history (atomically), with a note of when it was fetched."""
    target = path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    history.frame.write_parquet(tmp)
    tmp.replace(target)
    meta = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": URL,
        "first": str(history.frame["date"][0]),
        "last": str(history.frame["date"][-1]),
        "days": history.frame.height,
        "gaps": [[str(a), str(b)] for a, b in history.gaps],
    }
    target.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    return target


def load(data_dir: Path | str) -> pl.DataFrame:
    stored = path(data_dir)
    if not stored.exists():
        raise ValueError("no Fear & Greed history stored; run kestrel ingest-sentiment first")
    return pl.read_parquet(stored)


def attach(bars: pl.DataFrame, history: pl.DataFrame, bar_length: timedelta) -> pl.DataFrame:
    """Add ``fear_greed``: the latest value known at each bar's close, or null when the
    newest known value is older than ``MAX_AGE``."""
    known = history.select(
        known_at=(pl.col("date").cast(pl.Datetime("us", "UTC")) + KNOWN_AFTER),
        fear_greed=pl.col("value"),
    ).sort("known_at")
    with_close = bars.with_columns(_close=pl.col("open_time") + bar_length).sort("_close")
    joined = with_close.join_asof(known, left_on="_close", right_on="known_at", strategy="backward")
    fresh = (pl.col("_close") - pl.col("known_at")) <= MAX_AGE
    return (
        joined.with_columns(fear_greed=pl.when(fresh).then(pl.col("fear_greed")).otherwise(None))
        .drop("_close", "known_at")
        .sort("open_time")
    )
