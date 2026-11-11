"""Monday range pullback (research): backlog S1, rebuilt long only with no added filters.

Days are Brisbane days (AEST, UTC+10, no daylight saving). Monday's range is the high and
low of all Monday bars; it is only known once Monday's last bar has closed, and a week
whose Monday has a missing bar is skipped.

From Tuesday, the week turns bullish when a bar closes above Monday's high before any bar
closes below Monday's low (a close below first makes it bearish: no trade that week).
After a bullish break, the first bar whose low comes within ``tolerance_bps`` of Monday's
midpoint, and that closes no more than that far below it, signals a long. The breaking
bar itself never does. Stop: Monday's low. No target: the trade is closed at the end of
``exit_day`` (ISO weekday, 5 = Friday) by an exit signal on that day's last bar, or by
its stop. At most one trade a week, and no entry on or after the exit bar.

This is the version the S1 rebuild spec asks for: only the "pullback to the midpoint in a
bullish week" setup, with none of the filters (RSI, SMA, ATR, time of day) the original
stacked on after seeing results.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

AEST = timedelta(hours=10)


class MondayMidPullback:
    def __init__(self, symbol: str = "BTCUSDT", tolerance_bps: int = 50, exit_day: int = 5) -> None:
        if not 2 <= exit_day <= 7:
            raise ValueError("exit_day must be an ISO weekday from 2 (Tuesday) to 7 (Sunday)")
        self.symbol = symbol
        self.tol = tolerance_bps / 1e4
        self.exit_day = exit_day
        self.name = f"Monday range pullback {tolerance_bps}bps {symbol}"

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        n = bars.height
        enter, exit_ = [False] * n, [False] * n
        stop: list[float | None] = [None] * n
        if n >= 2:
            step = bars["open_time"].diff().median()
            assert isinstance(step, timedelta)
            if step > timedelta(hours=1):
                raise ValueError(f"{self.name} needs bars of 1h or shorter, so AEST days align")
            self._run(bars, step, enter, stop, exit_)
        return pl.DataFrame(
            {"enter": enter, "stop": stop, "target": [None] * n, "exit": exit_},
            schema={
                "enter": pl.Boolean,
                "stop": pl.Float64,
                "target": pl.Float64,
                "exit": pl.Boolean,
            },
        )

    def _run(
        self,
        bars: pl.DataFrame,
        step: timedelta,
        enter: list[bool],
        stop: list[float | None],
        exit_: list[bool],
    ) -> None:
        per_day = round(timedelta(days=1) / step)
        times = bars["open_time"].to_list()
        highs, lows, closes = bars["high"].to_list(), bars["low"].to_list(), bars["close"].to_list()
        week: date | None = None
        mon_high = mon_low = 0.0
        mon_bars = 0
        bias: str | None = None
        traded = False
        for t, opened in enumerate(times):
            local = opened + AEST
            day = local.date()
            weekday = day.isoweekday()
            this_week = day - timedelta(days=weekday - 1)
            if this_week != week:
                week, mon_high, mon_low, mon_bars = this_week, highs[t], lows[t], 0
                bias, traded = None, False
            if weekday == 1:
                mon_high, mon_low = max(mon_high, highs[t]), min(mon_low, lows[t])
                mon_bars += 1
                continue
            last_of_day = (local + step).date() != day
            if weekday > self.exit_day or (weekday == self.exit_day and last_of_day):
                exit_[t] = True  # sells at the next open; no new entries this week
                continue
            if mon_bars != per_day:
                continue  # no complete Monday, no range
            c = closes[t]
            if bias is None:
                bias = "up" if c > mon_high else "down" if c < mon_low else None
                continue
            mid = (mon_high + mon_low) / 2
            if (
                bias == "up"
                and not traded
                and lows[t] <= mid * (1 + self.tol)
                and c >= mid * (1 - self.tol)
                and c > mon_low
            ):
                enter[t], stop[t], traded = True, mon_low, True
