"""Turn of the month (research, YouTube batch 2 A3): hold only over the month's change.

In equities, returns cluster in the last trading day of a month and the first few of the
next (Ariel 1987; Lakonishok and Smidt 1988; McConnell and Xu 2008), usually put down to
month-end pay and fund flows. This tests whether BTC shows the same.

Daily bars, UTC days. The coin is held during the last ``last_days`` days of each month
and the first ``first_days`` days of the next, and in cash otherwise. The weight decided
at a bar's close is for the next day, so it is 1 when the *next* day falls in the window.
Calendar dates are known in advance, so reading tomorrow's date is not lookahead.

The video's base rule (buy the last day's close, sell the first day's close) holds only
the first day: ``last_days=0, first_days=1``. Its variant is ``last_days=5,
first_days=3``.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

import polars as pl


def in_window(day: date, last_days: int, first_days: int) -> bool:
    """Is ``day`` among the last ``last_days`` or first ``first_days`` days of its month?"""
    month_length = calendar.monthrange(day.year, day.month)[1]
    return day.day <= first_days or day.day > month_length - last_days


class TurnOfMonth:
    def __init__(self, symbol: str = "BTCUSDT", last_days: int = 0, first_days: int = 1) -> None:
        if last_days < 0 or first_days < 0 or last_days + first_days == 0:
            raise ValueError("hold at least one day: last_days + first_days must be positive")
        self.symbol = symbol
        self.last_days, self.first_days = last_days, first_days
        self.name = f"turn of the month -{last_days}/+{first_days} {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        if bars.height >= 2 and bars["open_time"].diff().median() != timedelta(days=1):
            raise ValueError(f"{self.name} needs daily bars")
        weights = [
            1.0 if in_window(opened.date() + timedelta(days=1), self.last_days, self.first_days)
            else 0.0
            for opened in bars["open_time"]
        ]  # fmt: skip
        return pl.Series("weight", weights, dtype=pl.Float64)
