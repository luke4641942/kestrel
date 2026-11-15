"""Information beyond prices that a strategy can ask for, attached to its bars.

A strategy names the features it needs (StrategySpec.features); every caller that runs a
strategy (backtests, signals, walk-forward, robustness) attaches them the same way through
``attach_features``, so a backtest and a chart can never see different data. Each feature
module decides when its values are known (kestrel.data.sentiment, kestrel.data.funding),
and only values known by a bar's close are attached to it. Market-wide features ignore the
symbol; per-coin ones (funding) read that coin's history.

``stablecoins_up`` is 1.0 when the stablecoin supply known at the close grew over 4 weeks
(kestrel.data.stablecoins), 0.0 when it didn't. Market-wide, like Fear & Greed, so it can
also go on a coin panel (``MARKET_WIDE``).

``btc_close`` is BTC's close on the same bar (matched by open time), so it is known at the
same moment as the bar's own close. A bar with no BTC bar gets null, never a neighbour's.

``regime_volatile`` is 1.0 on a day the stored regime fit (kestrel.regimes.RegimeStore)
believed, that day, that the market was in its most volatile state, else 0.0; null before
the fit's first state. Every state is filtered (inputs up to that day only), so it is known
at the day's close. Bars past the fit's last day are refused rather than left null: a
strategy filtered by regimes must not quietly stop filtering when the fit is out of date.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from kestrel.data import funding, sentiment, stablecoins
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.regimes import RegimeStore

BTC = "BTCUSDT"
BTC_CLOSE = "btc_close"
REGIME_VOLATILE = "regime_volatile"
FEATURES = (sentiment.FEATURE, funding.FEATURE, BTC_CLOSE, REGIME_VOLATILE, stablecoins.FEATURE)
MARKET_WIDE = (sentiment.FEATURE, stablecoins.FEATURE)
"""Features that are the same for every coin on a day, so a coin panel can carry them."""


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
        elif feature == REGIME_VOLATILE:
            bars = attach_regime_volatile(bars, symbol, interval, data_dir)
        elif feature == stablecoins.FEATURE:
            if interval is not Interval.D1:
                raise ValueError(f"{feature} is daily; it needs 1d bars")
            bars = stablecoins.attach(bars, stablecoins.load(data_dir))
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


def attach_regime_volatile(
    bars: pl.DataFrame, symbol: str, interval: Interval, data_dir: Path | str
) -> pl.DataFrame:
    if interval is not Interval.D1:
        raise ValueError("regimes are fitted on daily bars")
    if bars.is_empty():
        return bars.with_columns(pl.lit(None, pl.Float64).alias(REGIME_VOLATILE))
    try:
        fit = RegimeStore(data_dir).load(symbol.upper())
    except KeyError:
        raise ValueError(
            f"no regime fit for {symbol.upper()}; fit it first (kestrel regimes, or the "
            "Regimes page)"
        ) from None
    last_fitted = datetime.fromtimestamp(fit["time"][-1], UTC)
    newest: datetime = bars["open_time"].max()  # type: ignore[assignment]
    if newest > last_fitted:
        raise ValueError(
            f"the {symbol.upper()} regime fit ends {last_fitted:%Y-%m-%d}; the bars run to "
            f"{newest:%Y-%m-%d}. Refit before using regimes on later bars"
        )
    volatile = len(fit["states"]) - 1  # states are sorted calmest first
    states = pl.DataFrame(
        {
            "open_time": [datetime.fromtimestamp(t, UTC) for t in fit["time"]],
            REGIME_VOLATILE: [None if s is None else float(s == volatile) for s in fit["state"]],
        },
        schema={"open_time": pl.Datetime("us", "UTC"), REGIME_VOLATILE: pl.Float64},
    )
    return bars.join(states, on="open_time", how="left").sort("open_time")
