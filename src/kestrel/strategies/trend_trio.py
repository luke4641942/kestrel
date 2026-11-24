"""Trend-vol-target on BTC, ETH and SOL at once (research, 2026-11-15): one account, thirds.

The three single-coin strategies on pursue run as one portfolio, the way they would share a
wallet: each coin gets a third of equity, and inside its third trend-vol-target sizes it
exactly as it does alone (``weight = trend x min(1, target / volatility)``), so the coin's
weight is a third of that. A coin with no bars yet (SOL before 2020-08) leaves its third in
cash. Decided at each daily close; the portfolio engine rebalances a coin when its target
moves more than the no-trade band.

The weights come from a rule (equal thirds), as docs/rules.md asks of a multi-strategy
portfolio; a different split would be a different trial. ``TrioHold`` is the control: a
third of each coin, rebalanced each Sunday once all three trade.
"""

from __future__ import annotations

import polars as pl

from kestrel.strategies.basket import DECISIONS, no_decisions, require_daily
from kestrel.strategies.trend import TrendVolTarget

TRIO = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


class TrendTrio:
    def __init__(
        self,
        symbol: str = "UNIVERSE",
        fast_days: int = 20,
        mid_days: int = 60,
        slow_days: int = 120,
        vol_days: int = 30,
        target_vol_pct: int = 50,
    ) -> None:
        self.symbol = symbol
        self.params = dict(
            fast_days=fast_days, mid_days=mid_days, slow_days=slow_days,
            vol_days=vol_days, target_vol_pct=target_vol_pct,
        )  # fmt: skip
        self.name = f"trend-vol-target on BTC, ETH and SOL, a third each ({target_vol_pct}%)"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        coins = panel.filter(pl.col("symbol").is_in(TRIO))
        if coins.is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        parts = []
        for (sym,), bars in coins.sort("open_time").group_by(["symbol"], maintain_order=True):
            w = TrendVolTarget(str(sym), **self.params).target_weights(bars)
            parts.append(bars.select("open_time", "symbol").with_columns(weight=w / len(TRIO)))
        weights = pl.concat(parts)
        days = coins.select("open_time").unique()
        held = weights.filter(pl.col("weight") > 0)
        idle = days.join(held, on="open_time", how="anti").with_columns(
            symbol=pl.lit(None, pl.Utf8), weight=pl.lit(0.0)
        )
        out = pl.concat([held, idle.select(held.columns)])
        return out.select([pl.col(c).cast(t) for c, t in DECISIONS.items()]).sort(
            "open_time", "symbol", nulls_last=True
        )


class TrioHold:
    """The control: a third of each of BTC, ETH and SOL (a third in cash before SOL lists),
    rebalanced each Sunday."""

    def __init__(self, symbol: str = "UNIVERSE") -> None:
        self.symbol = symbol
        self.name = "BTC, ETH and SOL held, a third each"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        coins = panel.filter(pl.col("symbol").is_in(TRIO))
        if coins.is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        sundays = coins.filter(pl.col("open_time").dt.weekday() == 7)
        out = sundays.select("open_time", "symbol").with_columns(weight=pl.lit(1 / len(TRIO)))
        return out.select([pl.col(c).cast(t) for c, t in DECISIONS.items()]).sort(
            "open_time", "symbol"
        )
