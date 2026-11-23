"""Time-series momentum across forex pairs (P6, the first forex note).

The textbook rule (Moskowitz, Ooi and Pedersen 2012, "Time series momentum"), on spot
prices: at each day's close, each pair is long if its price rose over the last
``lookback_days`` trading days and short if it fell, sized so that the pair alone would
run at ``target_vol_pct`` a year, judged by the volatility of its last ``vol_days`` daily
returns (annualised over 260 trading days). If the sum of |weights| would pass
``max_gross_pct`` / 100, every weight is scaled down by the same factor.

Disclosed differences from the paper: the signal is the spot price change, without the
interest-rate carry a futures return would include (carry is charged and reported by the
engine, not traded on); volatility is a simple rolling window, not an exponential one;
weights are recomputed daily and traded through the no-trade band rather than monthly.

Each pair is computed on its own candles, so nothing is computed across another pair's
holiday; on a day a pair has no candle its weight stands (the engine doesn't trade it).
Every input is a past close, so the weight for a day uses that day's close and earlier.
"""

from __future__ import annotations

import math

import polars as pl

from kestrel.data.rates import Rates
from kestrel.fx_strategy import FxSpec, calendar, register
from kestrel.strategy import Param

TRADING_DAYS = 260


class FxTrend:
    def __init__(
        self,
        lookback_days: int = 252,
        vol_days: int = 60,
        target_vol_pct: int = 3,
        max_gross_pct: int = 300,
    ) -> None:
        self.lookback, self.vol_days = lookback_days, vol_days
        self.target = target_vol_pct / 100
        self.cap = max_gross_pct / 100
        self.name = (
            f"forex trend {lookback_days}d, {target_vol_pct}% vol per pair, cap {self.cap:g}x"
        )

    def _pair(self, frame: pl.DataFrame) -> pl.DataFrame:
        c = pl.col("close")
        r = (c / c.shift(1)).log()
        vol = r.rolling_std(self.vol_days) * math.sqrt(TRADING_DAYS)
        trend = (c / c.shift(self.lookback)).log()
        weight = pl.when(vol > 0).then(trend.sign() * self.target / vol).otherwise(None)
        return frame.select("open_time", weight.alias("w"))

    def target_weights(self, bars: dict[str, pl.DataFrame], rates: Rates) -> pl.DataFrame:
        days = pl.DataFrame(
            {"open_time": calendar(bars)}, schema={"open_time": pl.Datetime("us", "UTC")}
        )
        out = days
        for pair in sorted(bars):
            w = self._pair(bars[pair]).rename({"w": pair})
            out = out.join(w, on="open_time", how="left")
        pairs = sorted(bars)
        out = out.sort("open_time").with_columns(pl.col(pairs).forward_fill().fill_null(0.0))
        gross = pl.sum_horizontal(pl.col(pairs).abs())
        scale = pl.when(gross > self.cap).then(self.cap / gross).otherwise(1.0)
        return out.with_columns([(pl.col(p) * scale).alias(p) for p in pairs])


register(
    FxSpec(
        "fx:trend",
        "Time-series momentum across the forex pairs: long a pair whose price rose over the "
        "lookback, short one that fell, each sized to a volatility target, scaled to the "
        "gross cap. Research: runs only through its frozen note (kestrel fx-backtest).",
        FxTrend,
        (
            Param("lookback_days", 252, 20, 520, "Trading days the trend is measured over"),
            Param("vol_days", 60, 10, 260, "Trading days of returns that measure volatility"),
            Param("target_vol_pct", 3, 1, 20, "Annual volatility each pair is sized to, %"),
            Param("max_gross_pct", 300, 50, 300, "Largest sum of |weights|, % of equity"),
        ),
    )
)
