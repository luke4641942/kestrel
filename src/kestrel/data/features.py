"""Information beyond prices that a strategy can ask for, attached to its bars.

A strategy names the features it needs (StrategySpec.features); every caller that runs a
strategy (backtests, signals, walk-forward, robustness) attaches them the same way through
``attach_features``, so a backtest and a chart can never see different data. Each feature
module decides when its values are known (kestrel.data.sentiment, kestrel.data.funding),
and only values known by a bar's close are attached to it. Market-wide features ignore the
symbol; per-coin ones (funding) read that coin's history.

``btc_close`` is BTC's close on the same bar (matched by open time), so it is known at the
same moment as the bar's own close. A bar with no BTC bar gets null, never a neighbour's.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl

from kestrel.data import funding, sentiment
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore

BTC = "BTCUSDT"
BTC_CLOSE = "btc_close"
FEATURES = (sentiment.FEATURE, funding.FEATURE, BTC_CLOSE)


def attach_features(
    bars: pl.DataFrame,
    features: tuple[str, ...],
    symbol: str,
    interval: Interval,
    data_dir: Path | str,
) -> pl.DataFrame:
    for feature in features:
        if feature == sentiment.FEATURE:
            bars = sentiment.attach(bars, sentiment.load(data_dir), interval.duration)
        elif feature == funding.FEATURE:
            bars = funding.attach(bars, funding.load(data_dir, symbol), interval.duration)
        elif feature == BTC_CLOSE:
            bars = attach_btc_close(bars, interval, data_dir)
        else:
            raise ValueError(f"unknown feature {feature!r}; one of {', '.join(FEATURES)}")
    return bars


def attach_btc_close(bars: pl.DataFrame, interval: Interval, data_dir: Path | str) -> pl.DataFrame:
    if bars.is_empty():
        return bars.with_columns(pl.lit(None, pl.Float64).alias(BTC_CLOSE))
    first = bars["open_time"].min()
    assert isinstance(first, datetime)
    btc = ParquetStore(data_dir).read(BTC, interval, first, None)
    if btc.is_empty():
        raise ValueError(f"no stored {BTC} {interval.value} bars; ingest them first")
    return bars.join(
        btc.select("open_time", pl.col("close").alias(BTC_CLOSE)), on="open_time", how="left"
    ).sort("open_time")
