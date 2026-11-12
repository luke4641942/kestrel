"""Each coin's bid-ask spread: an assumption banded by how much the coin trades.

Kestrel has no order-book history, and one assumed spread is wrong for most of a
cross-sectional universe: BTC quotes about a basis point, a mid-sized coin several. So a
coin's half-spread is set by its median daily quote volume over the last
``window`` days, known at that day's close and computed within one unbroken history (a
gap starts a new history: Binance reuses symbols). The bands live in kestrel.toml
([universe] spread_tiers); a coin too young to have a full window gets the widest band.

Not a measurement. Estimating spreads from daily highs and lows (Abdi and Ranaldo 2017)
was tried first and dropped: on daily bars a coin's daily range is hundreds of times its
spread, so the estimate is mostly noise, and clipping negative readings to zero biases it
upward. It gave BTC a median of 21 bps, against a real spread near 1, and charged a
momentum backtest about 47 bps per side. Research notes guard against these bands being
too low by requiring an edge to survive twice the cost actually paid.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl


def with_half_spread(
    frame: pl.DataFrame, window: int, tiers: Sequence[tuple[float, float]], by: list[str]
) -> pl.DataFrame:
    """Add ``half_spread`` (a fraction) known at each row's close, per group ``by``.

    ``tiers`` are (minimum median daily quote volume, half-spread as a fraction), highest
    volume first, the last starting at 0. The frame must be sorted by ``by`` then time."""
    volume = pl.col("quote_volume").rolling_median(window, min_samples=window).over(by)
    widest = max(spread for _, spread in tiers)
    band: pl.Expr = pl.lit(widest)
    for floor, spread in reversed(tiers):
        band = pl.when(volume >= floor).then(pl.lit(spread)).otherwise(band)
    return frame.with_columns(
        half_spread=pl.when(volume.is_null()).then(pl.lit(widest)).otherwise(band)
    )
