"""Each coin's bid-ask spread, estimated from its own daily bars.

Kestrel has no order-book history, and a single assumed spread is wrong for most of a
cross-sectional universe: BTC quotes about a basis point, a thin coin can quote fifty.
Abdi and Ranaldo (2017) estimate the spread from close, high and low alone. With
``c`` the log close and ``eta`` the log mid-range ``(log high + log low) / 2``:

    S^2 = 4 * E[(c_t - eta_t) * (c_t - eta_{t+1})]

The close sits near the bid or the ask; the two mid-ranges bracket the efficient price,
so the product isolates the bounce between them. Negative sample averages (noise) are
set to zero, as in the paper. The half-spread paid per order is ``S / 2``.

Point in time: the term for day t needs day t+1's range, so it is known at t+1's close.
The estimate at a day's close uses terms known by then only, averaged over the last
``window`` days of one unbroken history (a gap starts a new history: Binance reuses
symbols). It is never below ``floor``.
"""

from __future__ import annotations

import polars as pl


def with_half_spread(frame: pl.DataFrame, window: int, floor: float, by: list[str]) -> pl.DataFrame:
    """Add ``half_spread`` (a fraction) known at each row's close, per group ``by``.

    The frame must be sorted by ``by`` then time, with ``high``, ``low`` and ``close``."""
    c = pl.col("close").log()
    eta = (pl.col("high").log() + pl.col("low").log()) / 2
    term = 4 * (c.shift(1) - eta.shift(1)) * (c.shift(1) - eta)  # known at this row's close
    return (
        frame.with_columns(_ar_term=term.over(by))
        .with_columns(_ar_mean=pl.col("_ar_term").rolling_mean(window, min_samples=window).over(by))
        .with_columns(
            half_spread=(pl.col("_ar_mean").clip(lower_bound=0).sqrt() / 2)
            .clip(lower_bound=floor)
            .fill_null(floor)
        )
        .drop("_ar_term", "_ar_mean")
    )
