"""Breakouts across a coin basket (research, YouTube batch 2 B1/B2, Critical Trading).

Two long-only trend-following entries from the same portfolio rules:

- ``rule`` 1, Bollinger breakout: buy a close above the ``band_days``-day band at
  ``entry_sd`` standard deviations; sell a close below the band at ``exit_sd`` below the
  middle.
- ``rule`` 2, 20% from the low: buy a close more than 20% above the lowest low of the last
  ``range_days`` days; sell a close more than 20% below the highest high of that window.

Shared rules. Daily bars, the coin universe (delisted coins included). Eligible coins have
``min_history_days`` of unbroken history and a 30-day median quote volume of at least
``min_volume_musd`` million USDT. New entries only while BTC closes above its
``trend_days`` average (held coins aren't sold when it falls below). At most ``top_n``
coins at ``1 / top_n`` each; when more coins signal than there are free slots, the lowest
``vol_days`` volatility wins. A protective stop ``stop_atr`` x ATR(20) below the entry
close sells at a *close* below it: the portfolio engine trades at the next open on
closing decisions, so this stop is checked at the close, not inside the bar.

The original runs on S&P 500 / Russell 3000 stocks with 10 slots; 330 USDT allows 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl

from kestrel.strategies.basket import BY, DECISIONS, VOLUME_DAYS, no_decisions, require_daily

ATR_DAYS = 20
BAND_PCT = 0.2
"""B2's 20% from the low (entry) and from the high (exit)."""


@dataclass
class _Held:
    stop: float


class BasketBreakout:
    def __init__(
        self,
        symbol: str = "UNIVERSE",
        rule: int = 1,
        band_days: int = 100,
        entry_sd: int = 3,
        exit_sd: int = 1,
        range_days: int = 50,
        top_n: int = 3,
        stop_atr: int = 3,
        trend_days: int = 200,
        vol_days: int = 63,
        min_history_days: int = 180,
        min_volume_musd: int = 20,
    ) -> None:
        if rule not in (1, 2):
            raise ValueError("rule must be 1 (Bollinger breakout) or 2 (20% from the low)")
        self.symbol = symbol
        self.rule, self.band_days, self.entry_sd, self.exit_sd = rule, band_days, entry_sd, exit_sd
        self.range_days, self.top_n, self.stop_atr = range_days, top_n, stop_atr
        self.trend_days, self.vol_days = trend_days, vol_days
        self.min_history, self.min_volume = min_history_days, min_volume_musd * 1e6
        what = (
            f"{band_days}d {entry_sd}SD Bollinger breakout"
            if rule == 1
            else f"20% off the {range_days}d low"
        )
        self.name = f"{what}, top {top_n} coins"

    def _signals(self, panel: pl.DataFrame) -> pl.DataFrame:
        c, h, lo = pl.col("close"), pl.col("high"), pl.col("low")
        prev = c.shift(1).over(BY)
        tr = pl.max_horizontal(h - lo, (h - prev).abs(), (lo - prev).abs())
        if self.rule == 1:
            mid = c.rolling_mean(self.band_days).over(BY)
            sd = c.rolling_std(self.band_days, ddof=0).over(BY)
            enter, leave = c > mid + self.entry_sd * sd, c < mid - self.exit_sd * sd
        else:
            low_n = lo.rolling_min(self.range_days).over(BY)
            high_n = h.rolling_max(self.range_days).over(BY)
            enter, leave = c > (1 + BAND_PCT) * low_n, c < (1 - BAND_PCT) * high_n
        return panel.with_columns(
            _enter=enter.fill_null(False),
            _leave=leave.fill_null(False),
            _atr=tr.rolling_mean(ATR_DAYS).over(BY),
            _vol=(c / prev).log().rolling_std(self.vol_days).over(BY),
            _age=pl.int_range(1, pl.len() + 1).over(BY),
            _volume=pl.col("quote_volume")
            .rolling_median(VOLUME_DAYS, min_samples=VOLUME_DAYS)
            .over(BY),
        )

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        if panel.is_empty():
            return no_decisions()
        require_daily(panel, self.name)
        sig = self._signals(panel)
        btc = (
            sig.filter(pl.col("symbol") == "BTCUSDT")
            .with_columns(_up=pl.col("close") > pl.col("close").rolling_mean(self.trend_days))
            .select("open_time", "_up")
        )
        rows = (
            sig.join(btc, on="open_time", how="left")
            .with_columns(pl.col("_up").fill_null(False))
            .sort("open_time")
        )
        days: dict[datetime, list[dict[str, Any]]] = {}
        for r in rows.select(
            "open_time", "symbol", "close", "_enter", "_leave", "_atr", "_vol", "_age",
            "_volume", "_up",
        ).iter_rows(named=True):  # fmt: skip
            days.setdefault(r["open_time"], []).append(r)

        held: dict[str, _Held] = {}
        out: list[dict[str, Any]] = []
        for day, bars in days.items():
            today = {r["symbol"]: r for r in bars}
            before = set(held)
            for sym in list(held):
                bar = today.get(sym)
                if bar is None:
                    continue  # no bar today: the engine handles a coin that stops trading
                if bar["_leave"] or bar["close"] < held[sym].stop:
                    del held[sym]
            free = self.top_n - len(held)
            if free > 0 and bars and bars[0]["_up"]:
                ready = [
                    r for r in bars
                    if r["symbol"] not in held and r["_enter"] and r["_atr"] is not None
                    and r["_vol"] is not None and r["_age"] >= self.min_history
                    and r["_volume"] is not None and r["_volume"] >= self.min_volume
                ]  # fmt: skip
                ready.sort(key=lambda r: (r["_vol"], r["symbol"]))
                for r in ready[:free]:
                    held[r["symbol"]] = _Held(r["close"] - self.stop_atr * r["_atr"])
            if set(held) != before:
                if held:
                    # New coins get 1/top_n; coins already held are kept as they are (null
                    # weight), not trimmed back to an equal share.
                    out += [
                        {
                            "open_time": day,
                            "symbol": s,
                            "weight": None if s in before else 1 / self.top_n,
                        }
                        for s in held
                    ]
                else:
                    out.append({"open_time": day, "symbol": None, "weight": 0.0})
        if not out:
            return no_decisions()
        return pl.DataFrame(out, schema=DECISIONS).sort("open_time", "symbol", nulls_last=True)
