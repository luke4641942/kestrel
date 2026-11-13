"""BTC/ETH dual momentum (research): hold the stronger of the two, or cash.

Dual momentum (Antonacci) combines relative strength (which asset has done better) with
absolute momentum (whether that one is rising at all). Every Sunday's daily close (UTC)
is a decision: each of BTCUSDT and ETHUSDT has a return over the last ``lookback_days``
days; the one with the higher return is held fully if its return is positive, otherwise
the portfolio holds cash. One coin at a time, rebalanced weekly; long only, spot.

Runs on the coin universe (kestrel.data.universe) because it chooses between two coins,
but only ever reads and holds BTC and ETH. Cash until both have ``lookback_days`` of
unbroken history.
"""

from __future__ import annotations

import polars as pl

from kestrel.strategies.basket import BY, as_decisions, no_decisions, require_daily

PAIR = ("BTCUSDT", "ETHUSDT")


class DualMomentum:
    def __init__(self, symbol: str = "UNIVERSE", lookback_days: int = 90) -> None:
        self.symbol = symbol
        self.lookback = lookback_days
        self.name = f"BTC/ETH dual momentum, {lookback_days}d"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        pair = panel.filter(pl.col("symbol").is_in(PAIR))
        if pair.is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        close = pl.col("close")
        scored = pair.with_columns(_r=(close / close.shift(self.lookback)).over(BY) - 1).filter(
            pl.col("open_time").dt.weekday() == 7
        )
        complete = (
            scored.filter(pl.col("_r").is_not_null())
            .group_by("open_time")
            .agg(pl.len().alias("_n"))
            .filter(pl.col("_n") == len(PAIR))
        )
        best = (
            scored.join(complete, on="open_time")
            .sort(["open_time", "_r", "symbol"], descending=[False, True, False])
            .group_by("open_time", maintain_order=True)
            .first()
            .filter(pl.col("_r") > 0)
        )
        return as_decisions(panel, best.with_columns(weight=pl.lit(1.0)))
