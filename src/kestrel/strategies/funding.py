"""Funding crowding filters (research): step aside when leveraged longs are crowded.

When perpetual futures funding runs high, the market is full of leveraged longs paying to
stay in, and a fall can cascade into forced selling. Each strategy here takes a base
position and goes to cash while ``funding_7d`` (the coin's average funding over the last
7 days, annualised; kestrel.data.funding) is at or above ``crowded_pct`` percent a year.
An unknown reading (before the history starts, or stale) leaves the base position alone.

- ``FundingHold``: the base is buy-and-hold. Compared with holding.
- ``FundingTrend``: the base is trend-vol-target with its default settings. Compared with
  trend-vol-target alone.

The default threshold, 30% a year, is about three times Binance's default funding rate
(0.01% per 8 hours, about 11% a year): a round multiple of the exchange's neutral level,
not a value fitted to returns. Long only, spot; the perpetuals are only read.
"""

from __future__ import annotations

import polars as pl

from kestrel.data.funding import FEATURE
from kestrel.strategies.trend import TrendVolTarget


def crowded(bars: pl.DataFrame, crowded_pct: int, name: str) -> pl.Series:
    """True on bars whose known 7-day funding is at or above the threshold."""
    if FEATURE not in bars.columns:
        raise ValueError(f"{name} needs the {FEATURE} feature attached to its bars")
    return (bars[FEATURE] >= crowded_pct / 100).fill_null(False)


class FundingHold:
    def __init__(self, symbol: str = "BTCUSDT", crowded_pct: int = 30) -> None:
        self.symbol, self.crowded_pct = symbol, crowded_pct
        self.name = f"hold, out when funding >= {crowded_pct}% {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        out = ~crowded(bars, self.crowded_pct, self.name)
        return out.cast(pl.Float64).alias("weight")


class FundingTrend:
    def __init__(self, symbol: str = "BTCUSDT", crowded_pct: int = 30) -> None:
        self.symbol, self.crowded_pct = symbol, crowded_pct
        self.base = TrendVolTarget(symbol)
        self.name = f"trend-vol-target, out when funding >= {crowded_pct}% {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        stay = ~crowded(bars, self.crowded_pct, self.name)
        return (self.base.target_weights(bars) * stay.cast(pl.Float64)).alias("weight")
