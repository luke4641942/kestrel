"""Canonical kline (candle) schema.

Every data source is normalised into this one schema before it touches storage,
so research, backtesting and live code never deal with venue-specific quirks.

Conventions:
- All timestamps are timezone-aware UTC with microsecond precision.
- ``open_time`` is the inclusive start of the bar and is the bar's identity.
- Prices and volumes are Float64. Research tolerates float error; the execution
  layer will use exact decimals where it matters (order prices and quantities).
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum

import polars as pl

UTC_US = pl.Datetime(time_unit="us", time_zone="UTC")

KLINE_SCHEMA: dict[str, pl.DataType] = {
    "open_time": UTC_US,
    "open": pl.Float64(),
    "high": pl.Float64(),
    "low": pl.Float64(),
    "close": pl.Float64(),
    "volume": pl.Float64(),
    "quote_volume": pl.Float64(),
    "trade_count": pl.Int64(),
    "taker_buy_base_volume": pl.Float64(),
    "taker_buy_quote_volume": pl.Float64(),
}


class Interval(StrEnum):
    """Bar intervals supported by Binance's bulk data, in Binance's notation."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def duration(self) -> timedelta:
        unit = self.value[-1]
        n = int(self.value[:-1])
        return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


def conform(df: pl.DataFrame) -> pl.DataFrame:
    """Select, order and cast columns to the canonical schema. Raises if any are missing."""
    missing = set(KLINE_SCHEMA) - set(df.columns)
    if missing:
        raise ValueError(f"missing columns for kline schema: {sorted(missing)}")
    return df.select([pl.col(name).cast(dtype) for name, dtype in KLINE_SCHEMA.items()])
