"""ETH/BTC relative value (research, 2026-11-15): which of the two to hold, not whether.

Long-only spot can't short the ETH/BTC spread, but it can choose which coin it owns. Each
Sunday's daily close (UTC), the log of ETH's price in BTC is compared with its average
over the last ``lookback_days`` days, in standard deviations (a z-score):

- below ``-entry_tenths / 10``: ETH is unusually cheap against BTC; hold ETH in full;
- above ``+entry_tenths / 10``: ETH is unusually dear; hold BTC in full;
- in between: half each.

Always fully invested in crypto, so the comparison is ``BtcEthHalf``: half each, always,
rebalanced on the same Sundays. Both run on a panel of BTCUSDT and ETHUSDT only.
"""

from __future__ import annotations

import math

import polars as pl

from kestrel.strategies.basket import DECISIONS, no_decisions, require_daily

PAIR = ("BTCUSDT", "ETHUSDT")


def _sundays(panel: pl.DataFrame) -> pl.DataFrame:
    """Each Sunday with both coins' closes, as columns btc and eth."""
    wide = (
        panel.filter(pl.col("symbol").is_in(PAIR))
        .pivot(on="symbol", index="open_time", values="close")
        .sort("open_time")
    )
    for s in PAIR:
        if s not in wide.columns:
            wide = wide.with_columns(pl.lit(None, pl.Float64).alias(s))
    return wide.rename({"BTCUSDT": "btc", "ETHUSDT": "eth"})


def _rows(when: object, weights: dict[str, float]) -> list[dict[str, object]]:
    return [{"open_time": when, "symbol": s, "weight": w} for s, w in weights.items() if w > 0]


class EthBtcValue:
    def __init__(
        self, symbol: str = "UNIVERSE", lookback_days: int = 90, entry_tenths: int = 10
    ) -> None:
        self.symbol = symbol
        self.lookback, self.entry = lookback_days, entry_tenths / 10
        self.name = f"ETH/BTC value, {lookback_days}d, z {entry_tenths / 10:g}"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        if panel.filter(pl.col("symbol").is_in(PAIR)).is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        ratio = (
            _sundays(panel)
            .with_columns(_x=(pl.col("eth") / pl.col("btc")).log())
            .with_columns(
                _mean=pl.col("_x").rolling_mean(self.lookback),
                _sd=pl.col("_x").rolling_std(self.lookback),
            )
        )
        out: list[dict[str, object]] = []
        for t, x, mean, sd in ratio.select("open_time", "_x", "_mean", "_sd").iter_rows():
            if t.isoweekday() != 7 or None in (x, mean, sd) or not sd or math.isnan(sd):
                continue
            z = (x - mean) / sd
            weights = (
                {"ETHUSDT": 1.0} if z < -self.entry
                else {"BTCUSDT": 1.0} if z > self.entry
                else {"BTCUSDT": 0.5, "ETHUSDT": 0.5}
            )  # fmt: skip
            out += _rows(t, weights)
        if not out:
            return no_decisions()
        return pl.DataFrame(out, schema=DECISIONS).sort("open_time", "symbol")


class BtcEthHalf:
    """The control: half BTC, half ETH, rebalanced each Sunday once both trade."""

    def __init__(self, symbol: str = "UNIVERSE") -> None:
        self.symbol = symbol
        self.name = "BTC/ETH half each"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        if panel.filter(pl.col("symbol").is_in(PAIR)).is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        out: list[dict[str, object]] = []
        for t, btc, eth in _sundays(panel).select("open_time", "btc", "eth").iter_rows():
            if t.isoweekday() == 7 and btc is not None and eth is not None:
                out += _rows(t, {"BTCUSDT": 0.5, "ETHUSDT": 0.5})
        if not out:
            return no_decisions()
        return pl.DataFrame(out, schema=DECISIONS).sort("open_time", "symbol")
