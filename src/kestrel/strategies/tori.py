"""Tori Trades' trendline method, long only (research; note to be frozen: a chart-pattern
strategy, a rejected family, tested at Luke's request and disclosed as such).

Her rules (her videos; Tradezella and FXReplay's write-ups), made mechanical:

- **Resistance line.** Through two known swing highs A and B (B later and lower), touched
  at least ``min_touches`` times: a swing high from A to B (A and B included) counts as a
  touch when it is within ``touch_atr_pct``% of an ATR of the line. No close above the
  line from A until the signal bar. B is the latest known swing high; A is the most recent
  earlier swing high that makes such a line, at most ``max_line_bars`` bars before B.
- **Entry.** A close above the line (by ``breakout_atr_pct``% of an ATR; 0: any close
  above it). One attempt per line. The engine buys at the next open.
- **Stop.** The lowest low since B (the bottom the break started from).
- **No target.** She stays in "for the entirety of the trend": a new rising line through
  the two latest known swing lows formed since B (the later one higher) is the trailing
  stop, and a close below it ends the trade at the next open (the ``exit`` column). Until
  that line exists, the stop protects the trade.

Swings become known only ``swing_bars`` bars after they form (kestrel.strategies.geometry):
no hindsight. Sizing is the bracket engine's fixed risk (risk% of equity over the distance
to the stop, capped at 100%), as she sizes by fixed risk. Shorts (her futures side) can't
be taken on spot.
"""

from __future__ import annotations

import polars as pl

from kestrel.strategies.geometry import Point, Swings, atr, line_at


class ToriTrendline:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        swing_bars: int = 3,
        min_touches: int = 3,
        touch_atr_pct: int = 50,
        breakout_atr_pct: int = 0,
        max_line_bars: int = 180,
    ) -> None:
        self.symbol, self.k, self.touches = symbol, swing_bars, min_touches
        self.touch, self.margin = touch_atr_pct / 100, breakout_atr_pct / 100
        self.max_bars = max_line_bars
        self.name = f"Tori trendline break, {min_touches}+ touches, trailing line {symbol}"

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        high, low, close = bars["high"].to_list(), bars["low"].to_list(), bars["close"].to_list()
        n = len(close)
        atrs = atr(high, low, close)
        swings = Swings(high, low, self.k)
        enter = [False] * n
        stop: list[float | None] = [None] * n
        exit_ = [False] * n
        used: set[tuple[Point, Point]] = set()
        trade: dict[str, float | int] | None = None  # {"b": index of B, "stop": price}
        for t in range(n):
            swings.update(t)
            a = atrs[t]
            if trade is not None:
                # The trade ends at its stop (intrabar, the engine's job) or on a close
                # below the rising line drawn since B.
                if low[t] <= trade["stop"]:
                    trade = None
                else:
                    line = self.rising(swings.lows, int(trade["b"]))
                    if line is not None and close[t] < line_at(*line, t):
                        exit_[t] = True
                        trade = None
                continue
            if a is None:
                continue
            found = self.resistance(swings.highs, close, t, a)
            if found is None or found in used:
                continue
            if close[t] > line_at(*found, t) + self.margin * a:
                used.add(found)
                b = found[1][0]
                floor = min(low[b : t + 1])
                if 0 < floor < close[t]:
                    enter[t], stop[t] = True, floor
                    trade = {"b": b, "stop": floor}
        return pl.DataFrame(
            {"enter": enter, "stop": stop, "target": [None] * n, "exit": exit_},
            schema={"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64,
                    "exit": pl.Boolean},
        )  # fmt: skip

    def resistance(
        self, highs: list[Point], close: list[float], t: int, atr_now: float
    ) -> tuple[Point, Point] | None:
        """The falling line through the latest known swing high B and the most recent
        earlier, higher swing high A that gives it min_touches touches and no close above
        it since A (before bar t)."""
        if len(highs) < 2:
            return None
        b = highs[-1]
        for i in range(len(highs) - 2, -1, -1):
            pa = highs[i]
            if b[0] - pa[0] > self.max_bars:
                break
            if pa[1] <= b[1]:
                continue
            touches = sum(
                1
                for h in highs[i : len(highs)]
                if abs(h[1] - line_at(pa, b, h[0])) <= self.touch * atr_now
            )
            if touches < self.touches:
                continue
            if any(close[j] > line_at(pa, b, j) for j in range(pa[0], t)):
                continue
            return (pa, b)
        return None

    @staticmethod
    def rising(lows: list[Point], since: int) -> tuple[Point, Point] | None:
        """The rising line through the two latest known swing lows at or after ``since``."""
        recent = [p for p in lows if p[0] >= since]
        if len(recent) < 2:
            return None
        p1, p2 = recent[-2:]
        return (p1, p2) if p2[1] > p1[1] else None
