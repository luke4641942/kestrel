"""Public attention to a coin: daily English Wikipedia pageviews of its article.

Liu and Tsyvinski (Risks and Returns of Cryptocurrency, Review of Financial Studies 2021)
find investor attention is one of the two strongest crypto return predictors: a rise in
Google searches came before higher returns the next week. Google Trends has no clean long
daily history, so this uses Wikipedia pageviews instead (as Kristoufek, 2013, did for
Bitcoin): free, daily, from 2015-07-01, counting people (``agent=user``), not bots.

A day's count is published only after that day ends, so a value for day D is treated as
known from D + 2 days, 00:00 UTC: a bar closing at the end of day D + 1 may use it, a bar
closing at the end of day D may not. Gaps are reported, never filled.

Wikimedia asks every client to identify itself with a way to reach its owner; requests
carry the project's public repository URL.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user"
USER_AGENT = "kestrel-research/0.1 (https://github.com/luke4641942/kestrel)"
FIRST = date(2015, 7, 1)
"""Wikimedia's pageview history starts here."""
KNOWN_AFTER = timedelta(days=2)
"""A count for day D is known from D + 2 days, 00:00 UTC (published after D ends)."""
ARTICLES = {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum"}
SCHEMA = {"date": pl.Date, "views": pl.Int64}


class AttentionError(ValueError):
    """The published history is malformed: reported, never repaired."""


def fetch(article: str, start: date, end: date, http: httpx.Client) -> pl.DataFrame:
    """Daily pageviews of ``article`` from ``start`` to ``end`` inclusive."""
    url = f"{API}/{article}/daily/{start:%Y%m%d}00/{end:%Y%m%d}00"
    response = http.get(url, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    rows = [
        (datetime.strptime(i["timestamp"][:8], "%Y%m%d").date(), int(i["views"]))
        for i in response.json()["items"]
    ]
    frame = pl.DataFrame(rows, schema=SCHEMA, orient="row").sort("date")
    if frame["date"].n_unique() != frame.height:
        raise AttentionError(f"duplicate days in {article}'s pageviews")
    return frame


def gaps(frame: pl.DataFrame) -> list[date]:
    """Days missing between the first and last day."""
    if frame.height < 2:
        return []
    have = set(frame["date"].to_list())
    first, last = frame["date"].min(), frame["date"].max()
    assert isinstance(first, date)
    assert isinstance(last, date)
    return [
        first + timedelta(days=i)
        for i in range((last - first).days)
        if first + timedelta(days=i) not in have
    ]


def path(data_dir: Path | str, symbol: str) -> Path:
    return Path(data_dir) / "attention" / f"{symbol.upper()}.parquet"


def save(frame: pl.DataFrame, data_dir: Path | str, symbol: str) -> Path:
    target = path(data_dir, symbol)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    frame.write_parquet(tmp)
    tmp.replace(target)
    return target


def load(data_dir: Path | str, symbol: str) -> pl.DataFrame:
    target = path(data_dir, symbol)
    if not target.exists():
        raise AttentionError(
            f"no attention data for {symbol.upper()}; run kestrel ingest-attention first"
        )
    return pl.read_parquet(target)


def known_at(frame: pl.DataFrame) -> pl.DataFrame:
    """Each day's count with the instant it becomes known (UTC)."""
    return frame.with_columns(
        known=pl.col("date").cast(pl.Datetime("us")).dt.replace_time_zone("UTC") + KNOWN_AFTER
    )


def ingest(symbol: str, http: httpx.Client, data_dir: Path | str, end: date | None = None) -> Path:
    """The whole history for a market's article, up to ``end`` (default: yesterday)."""
    if symbol.upper() not in ARTICLES:
        raise AttentionError(
            f"no Wikipedia article mapped for {symbol}; one of {', '.join(ARTICLES)}"
        )
    end = end or (datetime.now(UTC).date() - timedelta(days=1))
    return save(fetch(ARTICLES[symbol.upper()], FIRST, end, http), data_dir, symbol)
