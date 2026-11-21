"""Coin events a strategy may act on, as columns of a coin panel (one row per coin and bar),
each known by the bar's close:

- ``delisting_announced``: True from the first bar whose close comes after Binance announced
  the coin's delisting (kestrel.data.delistings; announcements are stored from 2022-02, so
  earlier delistings show False until the coin stops trading);
- ``perp_days``: days since the coin's USDT-M perp first settled funding (XUSDT, or
  1000XUSDT), counted at the bar's close; null before it, or with no perp. Binance's funding
  archive starts 2020-01, so a perp first seen in its first days had listed earlier: it
  counts as old (``CENSORED_DAYS``), never as new.

Found for the altcoin portfolio's exit rules (findings 11 and 18).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from kestrel.data import delistings, funding

DELISTING = "delisting_announced"
PERP_DAYS = "perp_days"
COLUMNS = (DELISTING, PERP_DAYS)
ARCHIVE_START = datetime(2020, 1, 2, tzinfo=UTC)
CENSORED_DAYS = 10_000
"""``perp_days`` for a perp first seen at the archive's start: listed long before."""


def perp_first(data_dir: Path | str, coin: str) -> datetime | None:
    """When a coin's perp first settled funding, or None if none is stored."""
    for name in (coin, f"1000{coin}"):
        try:
            history = funding.load(data_dir, name)
        except ValueError:
            continue
        if history.height:
            first = history["time"].min()
            assert isinstance(first, datetime)
            return first
    return None


def attach(
    panel: pl.DataFrame,
    announcements: pl.DataFrame,
    perps: dict[str, datetime | None],
    bar: pl.Expr | None = None,
) -> pl.DataFrame:
    """Add ``delisting_announced`` and ``perp_days`` to a coin panel (``symbol``,
    ``open_time``; daily bars, so a bar closes a day after it opens). ``announcements``:
    ``coin`` (without the quote) and ``announced_at``; ``perps``: symbol -> first funding."""
    close = bar if bar is not None else pl.col("open_time") + pl.duration(days=1)
    first_notice = (
        announcements.group_by(pl.col("coin").str.to_uppercase() + "USDT")
        .agg(pl.col("announced_at").min())
        .rename({"coin": "symbol", "announced_at": "_announced"})
    )
    starts = pl.DataFrame(
        {
            "symbol": list(perps),
            "_perp": [
                None
                if t is None
                else (datetime(1990, 1, 1, tzinfo=UTC) if t < ARCHIVE_START else t)
                for t in perps.values()
            ],
        },
        schema={"symbol": pl.Utf8, "_perp": pl.Datetime("us", "UTC")},
    )
    days = (close - pl.col("_perp")).dt.total_days()
    return (
        panel.join(first_notice, on="symbol", how="left")
        .join(starts, on="symbol", how="left")
        .with_columns(
            (pl.col("_announced").is_not_null() & (pl.col("_announced") < close)).alias(DELISTING),
            pl.when(pl.col("_perp").is_null() | (pl.col("_perp") >= close))
            .then(None)
            .when(pl.col("_perp").dt.year() == 1990)
            .then(CENSORED_DAYS)
            .otherwise(days)
            .cast(pl.Int64)
            .alias(PERP_DAYS),
        )
        .drop("_announced", "_perp")
    )


def attach_stored(panel: pl.DataFrame, data_dir: Path | str) -> pl.DataFrame:
    """``attach`` from what's stored: the delisting announcements and each coin's funding."""
    try:
        announcements = delistings.load(data_dir)
    except ValueError:
        raise ValueError(
            "no delisting announcements stored; run kestrel ingest-delistings"
        ) from None
    coins = panel["symbol"].unique().to_list()
    return attach(panel, announcements, {c: perp_first(data_dir, c) for c in coins})
