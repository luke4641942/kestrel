"""RSI dip-buying (research, YouTube batch 2 A1/A2/A4 and batch 3 G2 merged).

Short-term mean reversion: buy a sharp short-term fall in an uptrend, sell on the first
bounce. Larry Connors' RSI(2) rules and their variants (Quantified Strategies, Rayner Teo)
are the textbook form on the S&P 500.

Daily bars. At a close, while in cash: buy (weight 1 from the next open) when RSI over
``rsi_bars`` closes is below ``rsi_below`` and, if ``trend_days`` is not 0, the close is
above its ``trend_days``-day average. While holding, sell at a close that is:

- above the previous bar's high (the "QS exit"), when ``exit_rsi_above`` is 0;
- or with RSI above ``exit_rsi_above``, when it is set (G2's exit);
- or, when ``max_days`` is not 0, the ``max_days``-th close since buying (a time stop).

The scale-in of A1 (a second half when RSI falls 5 more points) isn't built: it would add
settings without changing what is tested.
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl

from kestrel.indicators import rsi, sma


class RsiDip:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        rsi_bars: int = 2,
        rsi_below: int = 10,
        trend_days: int = 200,
        exit_rsi_above: int = 0,
        max_days: int = 0,
    ) -> None:
        self.symbol = symbol
        self.rsi_bars, self.rsi_below, self.trend_days = rsi_bars, rsi_below, trend_days
        self.exit_rsi_above, self.max_days = exit_rsi_above, max_days
        self.name = f"RSI({rsi_bars}) < {rsi_below} dip {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        if bars.height >= 2 and bars["open_time"].diff().median() != timedelta(days=1):
            raise ValueError(f"{self.name} needs daily bars")
        close = bars["close"]
        strength = rsi(close, self.rsi_bars).to_list()
        trend = sma(close, self.trend_days).to_list() if self.trend_days else None
        closes, highs = close.to_list(), bars["high"].to_list()
        out: list[float] = []
        held_since: int | None = None
        for t, c in enumerate(closes):
            r = strength[t]
            if held_since is not None:
                if self.exit_rsi_above:
                    bounced = r is not None and r > self.exit_rsi_above
                else:
                    bounced = c > highs[t - 1]
                if bounced or (self.max_days and t - held_since >= self.max_days):
                    held_since = None
            elif r is not None and r < self.rsi_below:
                above = trend is None or (trend[t] is not None and c > trend[t])
                if above:
                    held_since = t
            out.append(0.0 if held_since is None else 1.0)
        return pl.Series("weight", out, dtype=pl.Float64)
