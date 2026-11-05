"""Structure pullback: a market-structure entry in the style taught by Craig Percoco.

Long-only (spot). Each rule is fixed here so nothing is decided by eye:

1. Swing points. Bar j is a swing high if its high is above the `swing_bars` bars before
   it and not below the `swing_bars` bars after it (mirror image for swing lows). A swing
   is only known `swing_bars` bars later, when those later bars have closed.
2. Change of character (bullish). The most recent known swing high is lower than the one
   before it (a down-trend's lower high), and a bar closes above it.
3. The impulse leg runs from the lowest low since that swing high up to the highest high
   since the change of character.
4. Fair value gaps (bullish) in the leg: three bars where the third bar's low is above the
   first bar's high. The gap's midpoint is the level that matters.
5. Entry signal, at a bar's close: its low reaches the 61.8% retracement of the leg AND
   the midpoint of a fair value gap from the leg (which lies between the 50% and 78.6%
   retracements), and it closes back above the 78.6% retracement. The engine buys at the
   next open.
6. Stop at the leg's low; target at `target_r` times the risk from the signal close.
7. The setup is cancelled if a bar closes below the leg's low, or after `expiry_bars`
   bars without an entry. One entry per change of character.

Differences from the video, on purpose: no shorts (spot only); one timeframe (4h), not
15m+1m; market entry at the next open instead of a resting limit order at the level;
his RSI indicator is not used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

FIB_ENTRY = 0.618
FIB_SHALLOW = 0.5
FIB_DEEP = 0.786


@dataclass
class _Setup:
    started: int
    leg_low: float
    leg_low_at: int
    impulse_high: float
    gaps: list[float] = field(default_factory=list)
    """Midpoints of bullish fair value gaps inside the leg."""


class StructurePullback:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        swing_bars: int = 3,
        target_r: int = 4,
        expiry_bars: int = 30,
    ) -> None:
        self.symbol = symbol
        self.swing_bars, self.target_r, self.expiry_bars = swing_bars, target_r, expiry_bars
        self.name = f"structure pullback k{swing_bars} {target_r}R {symbol}"

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        high, low, close = bars["high"].to_list(), bars["low"].to_list(), bars["close"].to_list()
        n, k = len(close), self.swing_bars
        enter = [False] * n
        stop: list[float | None] = [None] * n
        target: list[float | None] = [None] * n

        swing_highs: list[tuple[int, float]] = []  # (bar, price), in order of confirmation
        setup: _Setup | None = None
        used_high: int | None = None  # the swing high whose break started the last setup

        for t in range(n):
            # 1. A swing at bar j = t - k becomes known once bar t has closed.
            j = t - k
            if j >= k:
                left, right = high[j - k : j], high[j + 1 : t + 1]
                if high[j] > max(left) and high[j] >= max(right):
                    swing_highs.append((j, high[j]))

            # 5-7. Manage an open setup with information up to the previous bar.
            if setup is not None:
                if close[t] < setup.leg_low or t - setup.started > self.expiry_bars:
                    setup = None
                else:
                    span = setup.impulse_high - setup.leg_low
                    fib_entry = setup.impulse_high - FIB_ENTRY * span
                    fib_shallow = setup.impulse_high - FIB_SHALLOW * span
                    fib_deep = setup.impulse_high - FIB_DEEP * span
                    in_zone = [g for g in setup.gaps if fib_deep <= g <= fib_shallow]
                    if (
                        low[t] <= fib_entry
                        and in_zone
                        and low[t] <= max(in_zone)
                        and close[t] > fib_deep
                        and close[t] > setup.leg_low
                    ):
                        enter[t] = True
                        stop[t] = setup.leg_low
                        target[t] = close[t] + self.target_r * (close[t] - setup.leg_low)
                        setup = None
                        continue
                    # The leg keeps extending until price pulls back into the zone.
                    setup.impulse_high = max(setup.impulse_high, high[t])
                    if t >= 2 and low[t] > high[t - 2] and t - 2 >= setup.leg_low_at:
                        setup.gaps.append((high[t - 2] + low[t]) / 2)

            # 2-4. A change of character starts a new setup.
            if setup is None and len(swing_highs) >= 2:
                (_, prev_high), (last_at, last_high) = swing_highs[-2], swing_highs[-1]
                if last_high < prev_high and close[t] > last_high and used_high != last_at:
                    leg_low_at = min(range(last_at, t + 1), key=lambda i: low[i])
                    setup = _Setup(
                        started=t,
                        leg_low=low[leg_low_at],
                        leg_low_at=leg_low_at,
                        impulse_high=high[t],
                        gaps=[
                            (high[i - 2] + low[i]) / 2
                            for i in range(max(leg_low_at + 2, 2), t + 1)
                            if low[i] > high[i - 2]
                        ],
                    )
                    used_high = last_at

        return pl.DataFrame(
            {"enter": enter, "stop": stop, "target": target},
            schema={"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64},
        )
