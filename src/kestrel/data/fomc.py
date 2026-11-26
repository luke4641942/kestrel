"""Scheduled FOMC statement days (for note does-the-dollar-fall-against-other-currencies-on).

``fomc_statements.csv`` lists the last day of every scheduled FOMC meeting from 2006 to
2022, the day its statement is released, read from the Federal Reserve's published
calendars on 2026-11-26 (each row keeps the page's heading and URL, so it can be checked).
Conference calls and meetings the Fed marks unscheduled are left out, and so is the
March 2020 meeting it cancelled (replaced by two unscheduled ones): 8 a year, 7 in 2020.

The statement comes at 14:00 New York time (14:15 before 2013), inside the day's forex bar
whichever way the bar is cut (17:00 New York or 00:00 UTC).
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

FILE = Path(__file__).with_name("fomc_statements.csv")
NEW_YORK = ZoneInfo("America/New_York")


def statement_days() -> list[date]:
    with FILE.open() as f:
        return [date.fromisoformat(row["statement_day"]) for row in csv.DictReader(f)]


def statement_instant(day: date) -> datetime:
    """14:00 New York on the statement day, in UTC."""
    return datetime(day.year, day.month, day.day, 14, tzinfo=NEW_YORK).astimezone(UTC)
