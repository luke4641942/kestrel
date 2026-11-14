"""Bollinger pullback across a coin basket (research, YouTube batch 3 G1, Rayner Teo).

Buy strong coins in an uptrend after an oversold close and a further intraday drop; sell
on the first bounce.

Daily bars, the coin universe (delisted coins included). At a coin's close, while it isn't
held:

- it is eligible with ``min_history_days`` of unbroken history and a 30-day median quote
  volume of at least ``min_volume_musd`` million USDT;
- its close is above its ``trend_days`` average (and, with ``btc_filter`` 1, BTC's close is
  above BTC's);
- its close is below the 20-day Bollinger band at ``band_sd_tenths`` / 10 standard
  deviations.

Such a coin gets a limit buy at ``depth_pct``% under that close, resting for the next day
only (kestrel.backtest.portfolio's limit entries: filled at the lower of the limit and
the open if the day's low reaches it). With more candidates than free slots, the highest
150-day return goes first. At most ``top_n`` coins at ``1 / top_n`` each.

A held coin is sold at the next open after a close with RSI(2) above 50, or after its
``max_days``-th close since the fill (a time stop). No price stop, as in the original.

The strategy tracks its own fills from the next day's low, as the engine does. If the
engine refuses a fill (an order under the exchange minimum, or not enough cash), the
strategy still counts the coin as held and its next decision buys it at the open; at 330
USDT in three slots this should be rare, and the run's rejections show it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import polars as pl

from kestrel.indicators import bollinger, rate_of_change, rsi, sma
from kestrel.strategies.basket import BY, VOLUME_DAYS, no_decisions, require_daily

BAND_DAYS = 20
EXIT_RSI = 50
RANK_DAYS = 150
SCHEMA: dict[str, pl.DataType] = {
    "open_time": pl.Datetime("us", "UTC"),
    "symbol": pl.Utf8(),
    "weight": pl.Float64(),
    "limit": pl.Float64(),
}


class BollingerPullback:
    def __init__(
        self,
        symbol: str = "UNIVERSE",
        band_sd_tenths: int = 25,
        depth_pct: int = 3,
        max_days: int = 10,
        top_n: int = 3,
        trend_days: int = 200,
        btc_filter: int = 0,
        min_history_days: int = 180,
        min_volume_musd: int = 20,
    ) -> None:
        self.symbol = symbol
        self.band_sd, self.depth = band_sd_tenths / 10, depth_pct / 100
        self.max_days, self.top_n, self.trend_days = max_days, top_n, trend_days
        self.btc_filter = bool(btc_filter)
        self.min_history, self.min_volume = min_history_days, min_volume_musd * 1e6
        self.name = (
            f"Bollinger pullback {band_sd_tenths / 10:g}SD, {depth_pct}% limit, top {top_n} coins"
        )

    def _per_coin(self, coin: pl.DataFrame) -> pl.DataFrame:
        close = coin["close"]
        return coin.select("open_time", "symbol", "close", "low").with_columns(
            _lower=bollinger(close, BAND_DAYS, self.band_sd).lower,
            _trend=sma(close, self.trend_days),
            _rsi=rsi(close, 2),
            _roc=rate_of_change(close, RANK_DAYS),
            _age=pl.int_range(1, pl.len() + 1),
            _volume=coin["quote_volume"].rolling_median(VOLUME_DAYS, min_samples=VOLUME_DAYS),
        )

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        if panel.is_empty():
            return no_decisions().with_columns(pl.lit(None, pl.Float64).alias("limit"))
        require_daily(panel, self.name)
        coins = pl.concat(
            [self._per_coin(c) for c in panel.sort("open_time").partition_by(BY)]
        ).sort("open_time")
        btc_up: dict[datetime, bool] = {}
        if self.btc_filter:
            btc = coins.filter(pl.col("symbol") == "BTCUSDT")
            btc_up = {
                t: c is not None and m is not None and c > m
                for t, c, m in btc.select("open_time", "close", "_trend").iter_rows()
            }
        days: dict[datetime, list[dict[str, Any]]] = {}
        for r in coins.iter_rows(named=True):
            days.setdefault(r["open_time"], []).append(r)

        index = {d: i for i, d in enumerate(days)}
        held: dict[str, datetime] = {}  # coin -> the day its limit filled
        pending: dict[str, float] = {}  # coin -> limit resting for today
        out: list[dict[str, Any]] = []
        for day, rows in days.items():
            today = {r["symbol"]: r for r in rows}
            changed = False
            for sym, limit in pending.items():  # yesterday's limits: filled today?
                bar = today.get(sym)
                if bar is not None and bar["low"] <= limit and len(held) < self.top_n:
                    held[sym] = day
                    changed = True
            pending = {}
            for sym, since in list(held.items()):
                bar = today.get(sym)
                if bar is None:
                    continue
                age = index[day] - index[since]  # closes since the fill
                strong = bar["_rsi"] is not None and bar["_rsi"] > EXIT_RSI
                if strong or age >= self.max_days:
                    del held[sym]
                    changed = True
            free = self.top_n - len(held)
            if free > 0 and (not self.btc_filter or btc_up.get(day, False)):
                ready = [
                    r for r in rows
                    if r["symbol"] not in held and r["_lower"] is not None
                    and r["_trend"] is not None and r["_roc"] is not None
                    and r["_age"] >= self.min_history
                    and r["_volume"] is not None and r["_volume"] >= self.min_volume
                    and r["close"] > r["_trend"] and r["close"] < r["_lower"]
                ]  # fmt: skip
                ready.sort(key=lambda r: (-r["_roc"], r["symbol"]))
                pending = {r["symbol"]: r["close"] * (1 - self.depth) for r in ready[:free]}
            if changed or pending:
                weight = 1 / self.top_n
                # Held coins are kept as they are (null weight), not trimmed to 1/top_n.
                out += [
                    {"open_time": day, "symbol": s, "weight": None, "limit": None} for s in held
                ]
                out += [
                    {"open_time": day, "symbol": s, "weight": weight, "limit": lim}
                    for s, lim in pending.items()
                ]
                if not held and not pending:
                    out.append({"open_time": day, "symbol": None, "weight": 0.0, "limit": None})
        if not out:
            return pl.DataFrame(schema=SCHEMA)
        return pl.DataFrame(out, schema=SCHEMA).sort("open_time", "symbol", nulls_last=True)
