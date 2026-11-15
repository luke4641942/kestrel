"""Stablecoin supply: the total of USD-pegged stablecoins in circulation, daily, from DefiLlama.

New dollars entering crypto arrive as stablecoins (USDT, USDC and others are minted when
someone deposits dollars), so supply growth is a measure of fresh demand. DefiLlama's
public API (``stablecoincharts/all``) gives the daily total from 2017-11-29, free.

Two cautions. A day's figure is treated as known from D + 2 days, 00:00 UTC, the same
conservative rule as attention and news: a bar closing at the end of day D + 1 may use
it. And the history is DefiLlama's reconstruction today, not what was published at the
time; revisions can't be ruled out. Gaps are reported, never filled.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

API = "https://stablecoins.llama.fi/stablecoincharts/all"
USER_AGENT = "kestrel-research/0.1 (https://github.com/luke4641942/kestrel)"
KNOWN_AFTER = timedelta(days=2)
SCHEMA = {"date": pl.Date, "supply": pl.Float64}


class StablecoinError(ValueError):
    """The published history is malformed: reported, never repaired."""


def fetch(http: httpx.Client) -> pl.DataFrame:
    """Every day's USD-pegged stablecoin supply, in USD."""
    response = http.get(API, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    rows = []
    for point in response.json():
        day = datetime.fromtimestamp(int(point["date"]), UTC).date()
        usd = (point.get("totalCirculatingUSD") or {}).get("peggedUSD")
        if usd is None:
            raise StablecoinError(f"no USD-pegged supply on {day}")
        rows.append((day, float(usd)))
    frame = pl.DataFrame(rows, schema=SCHEMA, orient="row").sort("date")
    if frame["date"].n_unique() != frame.height:
        raise StablecoinError("duplicate days in the stablecoin history")
    return frame


def gaps(frame: pl.DataFrame) -> list[date]:
    """Days missing between the first and last."""
    if frame.height < 2:
        return []
    have = set(frame["date"].to_list())
    first, last = frame["date"].min(), frame["date"].max()
    assert isinstance(first, date) and isinstance(last, date)  # noqa: PT018
    return [
        first + timedelta(days=i)
        for i in range((last - first).days)
        if first + timedelta(days=i) not in have
    ]


def path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "stablecoins" / "supply.parquet"


def save(frame: pl.DataFrame, data_dir: Path | str) -> Path:
    target = path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    frame.write_parquet(tmp)
    tmp.replace(target)
    return target


def load(data_dir: Path | str) -> pl.DataFrame:
    target = path(data_dir)
    if not target.exists():
        raise StablecoinError("no stablecoin data; run kestrel ingest-stablecoins first")
    return pl.read_parquet(target)
