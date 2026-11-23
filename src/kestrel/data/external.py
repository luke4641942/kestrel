"""Daily series from outside Binance, for raw-effect signal studies (kestrel.studies.
weekly_signal): Coinbase closes, Deribit's implied-volatility index, and FRED's dollar
liquidity series. Public endpoints, no keys.

Each is stored as ``data/external/<name>.parquet`` with columns ``date`` and ``value``.
A day is stamped by the day it covers; when it is known is the study's business (each
signal says). Gaps are reported, never filled; a malformed reply is an error.

- **Coinbase** (``coinbase:<product>``, e.g. ``coinbase:BTC-USD``): the daily candle's close
  (candles start 00:00 UTC, so the close is the price at the next 00:00, like a Binance
  daily bar's). Up to 300 candles a request.
- **Deribit DVOL** (``dvol:<currency>``): the 30-day implied volatility index of Deribit's
  options, annualised percent, daily close. From 2021-03.
- **FRED** (``fred:<series>``): a series' observations as FRED serves them today (revised
  figures, not the vintage published at the time: a caveat each note states).
"""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

from kestrel.atomic import write_atomically

USER_AGENT = "kestrel-research/0.1"
SCHEMA = {"date": pl.Date, "value": pl.Float64}
COINBASE = "https://api.exchange.coinbase.com/products/{product}/candles"
DERIBIT = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"


class ExternalError(ValueError):
    """A reply that can't be read: reported, never repaired."""


def _frame(rows: dict[date, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": sorted(rows), "value": [rows[d] for d in sorted(rows)]}, schema=SCHEMA
    )


def fetch_coinbase(
    product: str,
    http: httpx.Client,
    start: date,
    end: date,
    sleep: Callable[[float], None] = time.sleep,
) -> pl.DataFrame:
    """Daily closes of a Coinbase product from ``start`` to ``end`` (inclusive)."""
    rows: dict[date, float] = {}
    chunk = start
    while chunk <= end:
        stop = min(chunk + timedelta(days=299), end)
        r = http.get(
            COINBASE.format(product=product),
            params={
                "granularity": 86400,
                "start": f"{chunk.isoformat()}T00:00:00Z",
                "end": f"{stop.isoformat()}T00:00:00Z",
            },
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, list):
            raise ExternalError(f"coinbase {product}: unexpected reply {str(body)[:80]}")
        for candle in body:  # [time, low, high, open, close, volume]
            day = datetime.fromtimestamp(int(candle[0]), UTC).date()
            if start <= day <= end:
                rows[day] = float(candle[4])
        chunk = stop + timedelta(days=1)
        sleep(0.2)  # well under Coinbase's public rate limit
    return _frame(rows)


def fetch_dvol(currency: str, http: httpx.Client, start: date, end: date) -> pl.DataFrame:
    """Deribit's daily DVOL closes. The API pages backwards from ``end``."""
    rows: dict[date, float] = {}
    first = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp() * 1000)
    last: int | None = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp() * 1000)
    while last is not None and last >= first:
        r = http.get(
            DERIBIT,
            params={
                "currency": currency,
                "resolution": "1D",
                "start_timestamp": first,
                "end_timestamp": last,
            },
            headers={"User-Agent": USER_AGENT},
        )
        r.raise_for_status()
        result = r.json().get("result")
        if not isinstance(result, dict) or "data" not in result:
            raise ExternalError(f"dvol {currency}: unexpected reply")
        for ts, _open, _high, _low, close in result["data"]:
            rows[datetime.fromtimestamp(ts / 1000, UTC).date()] = float(close)
        nxt = result.get("continuation")
        last = None if nxt is None or nxt >= last else int(nxt)
    return _frame({d: v for d, v in rows.items() if start <= d <= end})


def fetch_fred(series: str, http: httpx.Client) -> pl.DataFrame:
    """A FRED series' observations; "." (no value) days are left out and reported as gaps."""
    r = http.get(FRED.format(series=series), headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    reader = csv.reader(io.StringIO(r.text))
    head = next(reader, None)
    if head is None or len(head) != 2 or head[1] != series:
        raise ExternalError(f"fred {series}: unexpected header {head}")
    rows: dict[date, float] = {}
    for day, value in reader:
        if value.strip() in ("", "."):
            continue
        rows[date.fromisoformat(day)] = float(value)
    return _frame(rows)


def path(data_dir: Path | str, name: str) -> Path:
    return Path(data_dir) / "external" / f"{name.replace(':', '_')}.parquet"


def save(frame: pl.DataFrame, data_dir: Path | str, name: str) -> Path:
    target = path(data_dir, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(target, lambda tmp: frame.write_parquet(tmp))
    return target


def load(data_dir: Path | str, name: str) -> pl.DataFrame:
    target = path(data_dir, name)
    if not target.exists():
        raise ExternalError(f"no {name} data; run kestrel ingest-external first")
    return pl.read_parquet(target)


def gaps(frame: pl.DataFrame, every: timedelta = timedelta(days=1)) -> list[date]:
    """Missing days (or weeks, for a weekly series) between the first and last."""
    if frame.height < 2:
        return []
    have = set(frame["date"].to_list())
    first, last = frame["date"].min(), frame["date"].max()
    assert isinstance(first, date) and isinstance(last, date)  # noqa: PT018
    out, d = [], first
    while d < last:
        if d not in have:
            out.append(d)
        d += every
    return out


# What `kestrel ingest-external` downloads, and how often each series is published.
SERIES: dict[str, timedelta] = {
    "coinbase:BTC-USD": timedelta(days=1),
    "coinbase:ETH-USD": timedelta(days=1),
    "coinbase:USDT-USD": timedelta(days=1),
    "dvol:BTC": timedelta(days=1),
    "dvol:ETH": timedelta(days=1),
    "fred:WALCL": timedelta(weeks=1),
    "fred:WTREGEN": timedelta(weeks=1),
    "fred:RRPONTSYD": timedelta(days=1),  # business days: weekend gaps are expected
}


def fetch(name: str, http: httpx.Client, start: date, end: date) -> pl.DataFrame:
    kind, _, ident = name.partition(":")
    if kind == "coinbase":
        return fetch_coinbase(ident, http, start, end)
    if kind == "dvol":
        return fetch_dvol(ident, http, start, end)
    if kind == "fred":
        return fetch_fred(ident, http).filter(pl.col("date") <= end)
    raise ExternalError(f"unknown series {name!r}")
