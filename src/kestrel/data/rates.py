"""Short-term interest rates per currency, for forex financing (P6).

Overnight financing on a forex position is roughly the difference between the two
currencies' short-term rates, less the broker's markup (kestrel.backtest.fx_engine). The
rates here are the OECD's 3-month interbank rates, monthly averages in percent, from FRED
(``IR3TIB01<country>M156N``), stored like the other outside series
(``data/external/fred_IR3TIB01USM156N.parquet``).

**Used for charging, not deciding.** A month's rate is the rate a broker charged over
that month, so financing in a backtest uses the month's own value. A strategy that
*decides* on rates (carry) must use only values published by then: monthly averages are
out early the next month at the soonest, so ``known_rate`` gives the month before last.

**Missing months** are reported, never hidden. The first ingest (2026-11-23) found one
blank in 2005-2022: USD, April 2020 (the OECD left it empty). Charging carries the
previous month's rate forward over a blank, for at most ``MAX_CARRY`` months, and counts
every day it did (``Rates.carried``) so a run reports it. A longer gap is an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import httpx
import polars as pl

from kestrel.data import external

COUNTRY = {
    "USD": "US",
    "EUR": "EZ",
    "JPY": "JP",
    "GBP": "GB",
    "CHF": "CH",
    "CAD": "CA",
    "AUD": "AU",
    "NZD": "NZ",
}
MAX_CARRY = 2
"""Most months a blank may be bridged by the month before it."""


class RatesError(ValueError):
    """A currency with no rate for a month, beyond what may be carried."""


def series(currency: str) -> str:
    try:
        return f"fred:IR3TIB01{COUNTRY[currency.upper()]}M156N"
    except KeyError:
        raise RatesError(f"no rate series for {currency!r}") from None


def _months_between(first: date, last: date) -> list[date]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


@dataclass(frozen=True)
class RateReport:
    currency: str
    months: int
    first: date | None
    last: date | None
    missing: list[date]
    """Months with no value between ``since`` and the last published month."""


def ingest(data_dir: Path | str, http: httpx.Client, since: date) -> list[RateReport]:
    """Download every currency's series, save it, and report blanks from ``since``."""
    reports = []
    for currency in COUNTRY:
        name = series(currency)
        frame = external.fetch_fred(name.removeprefix("fred:"), http)
        external.save(frame, data_dir, name)
        reports.append(report(currency, frame, since))
    return reports


def report(currency: str, frame: pl.DataFrame, since: date) -> RateReport:
    days: list[date] = frame["date"].to_list()
    if not days:
        return RateReport(currency, 0, None, None, [])
    have = set(days)
    start = max(since, days[0])
    missing = [m for m in _months_between(start, days[-1]) if m not in have]
    return RateReport(currency, len(days), days[0], days[-1], missing)


@dataclass
class Rates:
    """A ``RateFn`` for the forex engine: the month's rate as a fraction (0.05 is 5%)."""

    by_month: dict[str, dict[date, float]]
    carried: dict[tuple[str, date], int] = field(default_factory=dict)
    """(currency, month) bridged by an earlier month, and how many days asked for it."""

    @classmethod
    def load(cls, data_dir: Path | str, currencies: list[str] | None = None) -> Rates:
        wanted = currencies or list(COUNTRY)
        by_month: dict[str, dict[date, float]] = {}
        for currency in wanted:
            frame = external.load(data_dir, series(currency))
            by_month[currency] = dict(
                zip(frame["date"].to_list(), frame["value"].to_list(), strict=True)
            )
        return cls(by_month)

    def __call__(self, currency: str, at: datetime) -> float:
        months = self.by_month.get(currency)
        if months is None:
            raise RatesError(f"no rates loaded for {currency}")
        month = date(at.year, at.month, 1)
        if month in months:
            return months[month] / 100
        y, m = month.year, month.month
        for _ in range(MAX_CARRY):
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
            if date(y, m, 1) in months:
                key = (currency, month)
                self.carried[key] = self.carried.get(key, 0) + 1
                return months[date(y, m, 1)] / 100
        raise RatesError(f"no {currency} rate for {month:%Y-%m} or the {MAX_CARRY} months before")

    def known_rate(self, currency: str, at: datetime) -> float | None:
        """For a decision at ``at``: the latest monthly value published by then (the month
        before last), or None. Never the current or last month's average."""
        months = self.by_month.get(currency, {})
        y, m = at.year, at.month
        for _ in range(2):
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        cutoff = date(y, m, 1)
        known = [d for d in months if d <= cutoff]
        return months[max(known)] / 100 if known else None

    @property
    def carried_days(self) -> int:
        return sum(self.carried.values())
