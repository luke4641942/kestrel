"""Information beyond prices that a strategy can ask for, attached to its bars.

A strategy names the features it needs (StrategySpec.features); every caller that runs a
strategy (backtests, signals, walk-forward, robustness) attaches them the same way through
``attach_features``, so a backtest and a chart can never see different data. Each feature
module decides when its values are known (kestrel.data.sentiment, kestrel.data.funding),
and only values known by a bar's close are attached to it. Market-wide features ignore the
symbol; per-coin ones (funding) read that coin's history.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from kestrel.data import funding, sentiment
from kestrel.data.schema import Interval

FEATURES = (sentiment.FEATURE, funding.FEATURE)


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
        else:
            raise ValueError(f"unknown feature {feature!r}; one of {', '.join(FEATURES)}")
    return bars
