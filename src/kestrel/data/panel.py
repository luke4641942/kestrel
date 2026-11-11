"""Many coins' bars in one long frame: the input to cross-sectional strategies.

One row per (symbol, open_time), sorted by symbol then time, with a ``history`` number
that starts at 0 and increases at every gap in a coin's bars. Binance reuses symbols:
LUNAUSDT is the original LUNA until 2022-05-13, then after an 18-day gap a different coin.
Anything computed over time (returns, volatility, spreads, eligibility) must stay within
one history, never across a gap.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from kestrel.data.binance_vision import Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore

PANEL_COLUMNS = ("symbol", "open_time", "open", "high", "low", "close", "quote_volume")


def with_histories(panel: pl.DataFrame, interval: Interval) -> pl.DataFrame:
    """Sort by symbol and time and number each coin's unbroken histories."""
    gap = pl.col("open_time").diff().over("symbol") > interval.duration
    return panel.sort("symbol", "open_time").with_columns(
        history=gap.fill_null(False).cum_sum().over("symbol").cast(pl.Int32)
    )


def load_panel(
    store: ParquetStore, symbols: list[str], interval: Interval, start: Month, end: Month
) -> pl.DataFrame:
    """Every stored bar of ``symbols`` in ``start..end`` (inclusive months)."""
    after = end.next()
    lo = datetime(start.year, start.month, 1, tzinfo=UTC)
    hi = datetime(after.year, after.month, 1, tzinfo=UTC)
    frames = []
    for symbol in symbols:
        bars = store.read(symbol, interval, lo, hi)
        if bars.height:
            frames.append(bars.with_columns(symbol=pl.lit(symbol)).select(PANEL_COLUMNS))
    if not frames:
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "open_time": pl.Datetime("us", "UTC"),
                **{c: pl.Float64 for c in PANEL_COLUMNS[2:]},
                "history": pl.Int32,
            }
        )
    return with_histories(pl.concat(frames), interval)
