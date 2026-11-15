"""News sentiment: the daily average tone of world news coverage of a coin, from GDELT.

GDELT (the Global Database of Events, Language and Tone) reads news worldwide and scores
each article's tone, roughly -10 (very negative) to +10 (very positive). Its DOC 2.0 API
returns a daily average for a search term (``mode=timelinetone``), free, from 2017. This is
news, not social media: X's affordable API has no history, so Luke chose news instead
(2026-11-14).

A day's tone is complete only once the day ends, so the value for day D is used from
D + 2 days, 00:00 UTC, the same rule as attention: a bar closing at the end of day D + 1 may
use it. Gaps are reported, never filled.

GDELT asks for at most one request every five seconds and refuses bursts, so requests go
one month at a time with a pause between them, backing off when refused, and each month
is kept as it arrives so an interrupted fetch picks up where it stopped.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

API = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = "kestrel-research/0.1 (https://github.com/luke4641942/kestrel)"
FIRST = date(2017, 1, 1)
KNOWN_AFTER = timedelta(days=2)
QUERIES = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum"}
SCHEMA = {"date": pl.Date, "tone": pl.Float64}
PAUSE = 10.0
"""Seconds between requests; GDELT's stated limit is one per five."""
BACKOFF = (30.0, 60.0, 120.0, 240.0)
"""Waits after each refusal (HTTP 429) before giving up."""


class NewsError(ValueError):
    """The published history is malformed or unavailable: reported, never repaired."""


Sleep = Callable[[float], None]


def fetch_month(
    query: str, month: date, http: httpx.Client, sleep: Sleep | None = None
) -> pl.DataFrame:
    """Daily tone for ``query`` over the calendar month starting ``month``."""
    sleep = sleep or time.sleep
    after = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    params = {
        "query": query,
        "mode": "timelinetone",
        "format": "json",
        "startdatetime": f"{month:%Y%m%d}000000",
        "enddatetime": f"{after - timedelta(days=1):%Y%m%d}235959",
    }
    for wait in (*BACKOFF, None):
        response = http.get(API, params=params, headers={"User-Agent": USER_AGENT})
        if response.status_code != 429:
            break
        if wait is None:
            raise NewsError(f"GDELT kept refusing {month:%Y-%m} (rate limit); try again later")
        sleep(wait)
    response.raise_for_status()
    try:
        timeline = response.json().get("timeline") or []
    except ValueError:
        raise NewsError(
            f"GDELT returned no data for {month:%Y-%m}: {response.text[:120]}"
        ) from None
    points = timeline[0]["data"] if timeline else []
    rows = [(datetime.strptime(p["date"][:8], "%Y%m%d").date(), float(p["value"])) for p in points]
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def path(data_dir: Path | str, symbol: str) -> Path:
    return Path(data_dir) / "news" / f"{symbol.upper()}.parquet"


def load(data_dir: Path | str, symbol: str) -> pl.DataFrame:
    target = path(data_dir, symbol)
    if not target.exists():
        raise NewsError(f"no news data for {symbol.upper()}; run kestrel ingest-news first")
    return pl.read_parquet(target)


def _save(frame: pl.DataFrame, data_dir: Path | str, symbol: str) -> None:
    target = path(data_dir, symbol)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    frame.sort("date").write_parquet(tmp)
    tmp.replace(target)


def ingest(
    symbol: str,
    http: httpx.Client,
    data_dir: Path | str,
    end: date | None = None,
    sleep: Sleep | None = None,
    on_month: Callable[[date], None] = lambda _: None,
) -> pl.DataFrame:
    """Every month from 2017 to ``end`` not already stored, saved as each arrives."""
    # Looked up at call time, not bound as a default, so a test can replace time.sleep.
    sleep = sleep or time.sleep
    if symbol.upper() not in QUERIES:
        raise NewsError(f"no news query for {symbol}; one of {', '.join(QUERIES)}")
    end = end or (datetime.now(UTC).date() - timedelta(days=1))
    try:
        stored = load(data_dir, symbol)
    except NewsError:
        stored = pl.DataFrame(schema=SCHEMA)
    have = {(d.year, d.month) for d in stored["date"].to_list()}
    month = FIRST
    first_request = True
    while month <= end:
        if (month.year, month.month) not in have:
            if not first_request:
                sleep(PAUSE)
            first_request = False
            part = fetch_month(QUERIES[symbol.upper()], month, http, sleep)
            stored = pl.concat([stored, part]).unique("date").sort("date")
            _save(stored, data_dir, symbol)
            on_month(month)
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return stored
