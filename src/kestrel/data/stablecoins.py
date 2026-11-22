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

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

API = "https://stablecoins.llama.fi/stablecoincharts/all"
USER_AGENT = "kestrel-research/0.1 (https://github.com/luke4641942/kestrel)"
KNOWN_AFTER = timedelta(days=2)
SCHEMA = {"date": pl.Date, "supply": pl.Float64}
FEATURE = "stablecoins_up"
GROWTH_DAYS = 28


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


@dataclass(frozen=True)
class Refreshed:
    added: int
    """New days appended."""
    revised: int
    """Saved days DefiLlama now gives a different figure for (reported; the saved one kept)."""
    last: date | None
    largest: float = 0.0
    """The largest revision, as a fraction of the saved figure."""


def refresh(data_dir: Path | str, http: httpx.Client, needed: date) -> Refreshed:
    """For paper trading: make sure the saved supply reaches ``needed``, fetching only if it
    doesn't. New days are appended and saved days are never rewritten, so the file is what
    was known when each decision was made and a replay reads the same figures. A gap in the
    combined history is an error (reported, never filled)."""
    try:
        saved: pl.DataFrame | None = load(data_dir)
    except StablecoinError:
        saved = None
    if saved is not None and saved.height and saved["date"].max() >= needed:  # type: ignore[operator]
        return Refreshed(0, 0, saved["date"].max())  # type: ignore[arg-type]
    fresh = fetch(http)
    if saved is None or saved.is_empty():
        combined, revised, largest = fresh, 0, 0.0
    else:
        last = saved["date"].max()
        both = saved.join(fresh, on="date", suffix="_now")
        changed = both.filter(pl.col("supply") != pl.col("supply_now"))
        revised = changed.height
        largest = (
            float(
                ((changed["supply_now"] / changed["supply"]) - 1).abs().max() or 0.0  # type: ignore[arg-type]
            )
            if revised
            else 0.0
        )
        combined = pl.concat([saved, fresh.filter(pl.col("date") > last)])
    missing = gaps(combined)
    if missing:
        raise StablecoinError(
            f"the stablecoin history has {len(missing)} missing days (first {missing[0]})"
        )
    save(combined, data_dir)
    added = combined.height - (0 if saved is None else saved.height)
    return Refreshed(added, revised, combined["date"].max(), largest)  # type: ignore[arg-type]


def load(data_dir: Path | str) -> pl.DataFrame:
    target = path(data_dir)
    if not target.exists():
        raise StablecoinError("no stablecoin data; run kestrel ingest-stablecoins first")
    return pl.read_parquet(target)


def growing(open_times: list[datetime], supply: pl.DataFrame) -> list[bool | None]:
    """For each daily bar: was the supply known at its close above its value 28 days before?
    None when either figure is missing."""
    by_day = dict(zip(supply["date"].to_list(), supply["supply"].to_list(), strict=True))
    out: list[bool | None] = []
    for t in open_times:
        latest = (t + timedelta(days=1) - KNOWN_AFTER).date()
        now, then = by_day.get(latest), by_day.get(latest - timedelta(days=GROWTH_DAYS))
        out.append(None if now is None or then is None else now > then)
    return out


def attach(bars: pl.DataFrame, supply: pl.DataFrame) -> pl.DataFrame:
    """``stablecoins_up`` (1.0 growing, 0.0 not, null unknown) on each daily bar. Market-wide,
    so a panel of many coins gets the same value on every coin's bar of a day."""
    days = bars.select("open_time").unique().sort("open_time")
    flags = growing(days["open_time"].to_list(), supply)
    known = days.with_columns(
        pl.Series(FEATURE, [None if f is None else float(f) for f in flags], dtype=pl.Float64)
    )
    return bars.join(known, on="open_time", how="left")
