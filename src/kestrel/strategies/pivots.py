"""Pivot reversal (research): backlog S2 version C, rebuilt long only with no regime filter.

Pivots come from a zigzag with a ``swing_bps`` threshold. While price is falling, the
running low is tracked; a pivot low is confirmed only when a bar *closes* at least
``swing_bps`` above that low. That bar is the earliest moment the pivot could have been
known, so it is the signal: buy at the next open. (The original drew pivots on a finished
chart, which buys at the low itself: a centred window that uses bars after the pivot.)
After a pivot low, the running high is tracked until a close at least ``swing_bps`` below
it confirms a pivot high, and the search for the next low begins.

Stop ``stop_bps`` below the signal close; target ``target_bps`` above it (the original's
1.5% and 3%). One position at a time (the engine ignores signals while a trade is open).
"""

from __future__ import annotations

import polars as pl


class PivotReversal:
    def __init__(
        self,
        symbol: str = "ETHUSDT",
        swing_bps: int = 350,
        stop_bps: int = 150,
        target_bps: int = 300,
    ) -> None:
        self.symbol = symbol
        self.swing, self.stop, self.target = swing_bps / 1e4, stop_bps / 1e4, target_bps / 1e4
        levels = f"{stop_bps / 100:g}/{target_bps / 100:g}%"
        self.name = f"pivot reversal {swing_bps / 100:g}% swing, {levels} {symbol}"

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        highs, lows, closes = bars["high"].to_list(), bars["low"].to_list(), bars["close"].to_list()
        n = len(closes)
        enter = [False] * n
        stop: list[float | None] = [None] * n
        target: list[float | None] = [None] * n
        leg: str | None = None  # "up" after a pivot low, "down" after a pivot high
        lo = hi = 0.0
        for t in range(n):
            if t == 0:
                lo, hi = lows[0], highs[0]
                continue
            if leg != "up":
                lo = min(lo, lows[t])
            if leg != "down":
                hi = max(hi, highs[t])
            c = closes[t]
            if leg != "up" and c >= lo * (1 + self.swing):
                leg, hi = "up", highs[t]  # pivot low confirmed now
                enter[t], stop[t], target[t] = True, c * (1 - self.stop), c * (1 + self.target)
            elif leg != "down" and c <= hi * (1 - self.swing):
                leg, lo = "down", lows[t]  # pivot high confirmed now
        return pl.DataFrame(
            {"enter": enter, "stop": stop, "target": target},
            schema={"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64},
        )
