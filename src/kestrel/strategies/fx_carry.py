"""Currency carry across the forex pairs (P6, the second forex note).

Hold each pair towards its higher-yielding currency, but only where the two currencies'
interest rates differ by more than ``threshold_bps``: below that the broker's financing
markup (1% a year, kestrel.toml) takes the whole difference, so the position pays to be
held. The rates are the OECD 3-month interbank rates, as published by then: each decision
uses the month before last (``Rates.known_rate``), never the current month's average.

At the first trading day of each month (New York), for each pair: long if the base
currency's known rate exceeds the quote's by more than the threshold, short if it is
lower by more than it, else flat; sized to ``target_vol_pct`` a year by the volatility of
the pair's last ``vol_days`` daily returns as of that day (annualised over 260 trading
days). The weights then stand for the month, so the book trades about monthly. If the sum
of |weights| would pass ``max_gross_pct`` / 100, every weight is scaled down together.

Each pair is computed on its own candles; a pair with no candle on a day keeps its
weight. Every input is known by the close of the day it is used.
"""

from __future__ import annotations

import math

import polars as pl

from kestrel.data.fx import NEW_YORK
from kestrel.data.rates import Rates
from kestrel.fx_strategy import FxSpec, calendar, register
from kestrel.strategy import Param

TRADING_DAYS = 260


class FxCarry:
    def __init__(
        self,
        threshold_bps: int = 100,
        vol_days: int = 60,
        target_vol_pct: int = 3,
        max_gross_pct: int = 300,
    ) -> None:
        self.threshold = threshold_bps / 10_000
        self.vol_days = vol_days
        self.target = target_vol_pct / 100
        self.cap = max_gross_pct / 100
        self.name = (
            f"forex carry above {threshold_bps}bp, {target_vol_pct}% vol per pair, "
            f"cap {self.cap:g}x"
        )

    def _pair(self, pair: str, frame: pl.DataFrame, rates: Rates) -> pl.DataFrame:
        c = pl.col("close")
        vol = (c / c.shift(1)).log().rolling_std(self.vol_days) * math.sqrt(TRADING_DAYS)
        month = pl.col("open_time").dt.convert_time_zone(NEW_YORK).dt.truncate("1mo")
        days = frame.select("open_time", vol.alias("vol"), month.alias("month"))
        # The first trading day of each month decides; the rest of the month follows it.
        firsts = days.group_by("month", maintain_order=True).first()
        weights = []
        for t, v in zip(firsts["open_time"], firsts["vol"], strict=True):
            base, quote = rates.known_rate(pair[:3], t), rates.known_rate(pair[3:], t)
            if base is None or quote is None or v is None or not v > 0:
                weights.append(0.0)
                continue
            diff = base - quote
            side = 1.0 if diff > self.threshold else -1.0 if diff < -self.threshold else 0.0
            weights.append(side * self.target / v)
        decided = firsts.select("month").with_columns(pl.Series("w", weights, dtype=pl.Float64))
        return days.join(decided, on="month", how="left").select("open_time", "w")

    def target_weights(self, bars: dict[str, pl.DataFrame], rates: Rates) -> pl.DataFrame:
        pairs = sorted(bars)
        out = pl.DataFrame(
            {"open_time": calendar(bars)}, schema={"open_time": pl.Datetime("us", "UTC")}
        )
        for pair in pairs:
            w = self._pair(pair, bars[pair], rates).rename({"w": pair})
            out = out.join(w, on="open_time", how="left")
        out = out.sort("open_time").with_columns(pl.col(pairs).forward_fill().fill_null(0.0))
        gross = pl.sum_horizontal(pl.col(pairs).abs())
        scale = pl.when(gross > self.cap).then(self.cap / gross).otherwise(1.0)
        return out.with_columns([(pl.col(p) * scale).alias(p) for p in pairs])


register(
    FxSpec(
        "fx:carry",
        "Currency carry: hold each pair towards its higher-yielding currency where the known "
        "rate difference beats the financing markup, sized to a volatility target, decided "
        "monthly. Research: runs only through its frozen note (kestrel fx-backtest).",
        FxCarry,
        (
            Param("threshold_bps", 100, 0, 500, "Rate difference needed to hold, basis points"),
            Param("vol_days", 60, 10, 260, "Trading days of returns that measure volatility"),
            Param("target_vol_pct", 3, 1, 20, "Annual volatility each pair is sized to, %"),
            Param("max_gross_pct", 300, 50, 300, "Largest sum of |weights|, % of equity"),
        ),
    )
)
