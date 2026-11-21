"""Token unlock schedules, from DefiLlama's public dataset files (note
do-coins-fall-into-large-token-unlocks).

DefiLlama's emissions API is paid, but the files behind its unlock pages are public:

- ``emissionsProtocolsList``: every protocol with a schedule;
- ``emissions/<protocol>``: its schedule, one daily series of tokens unlocked per section
  (team, investors, public sale...), the sections' categories, and its CoinGecko id.

``ingest`` keeps, per protocol, the daily total unlocked (all sections added up) and the
part from insider sections (DefiLlama's ``insiders`` category: team, investors, advisors),
plus the CoinGecko id, in ``<data>/unlocks/schedules.parquet``. CoinGecko's public coin
list (``coins.parquet``) maps ids to tickers. Schedules are as documented on the day of
ingestion: a schedule revised since an unlock may show it differently from how it was
known then. Nothing is repaired: a file that can't be read is reported and left out.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

LIST_URL = "https://defillama-datasets.llama.fi/emissionsProtocolsList"
FILE_URL = "https://defillama-datasets.llama.fi/emissions/{protocol}"
COINS_URL = "https://api.coingecko.com/api/v3/coins/list"
INSIDERS = "insiders"

SCHEDULE_SCHEMA = {
    "protocol": pl.Utf8,
    "gecko_id": pl.Utf8,
    "day": pl.Date,
    "unlocked": pl.Float64,
    "insiders": pl.Float64,
}


def parse(protocol: str, doc: dict[str, Any]) -> pl.DataFrame:
    """One protocol's file as daily rows: tokens unlocked by each day (cumulative), all
    sections and insider sections only. Empty if it has no documented schedule."""
    insider_sections = set((doc.get("categories") or {}).get(INSIDERS, []))
    totals: dict[date, list[float]] = {}
    for section in (doc.get("documentedData") or {}).get("data", []):
        insider = section.get("label") in insider_sections
        for point in section.get("data", []):
            day = datetime.fromtimestamp(point["timestamp"], UTC).date()
            unlocked = float(point.get("unlocked") or 0.0)
            row = totals.setdefault(day, [0.0, 0.0])
            row[0] += unlocked
            if insider:
                row[1] += unlocked
    rows = [(protocol, doc.get("gecko_id"), d, u, i) for d, (u, i) in sorted(totals.items())]
    return pl.DataFrame(rows, schema=SCHEDULE_SCHEMA, orient="row")


def ingest(
    http: httpx.Client,
    data_dir: Path | str,
    pause: float = 0.2,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, list[str]]:
    """Fetch every schedule and CoinGecko's coin list; write both. Returns (schedules,
    coins, problems)."""
    protocols: list[str] = http.get(LIST_URL).raise_for_status().json()
    frames, problems = [], []
    for k, name in enumerate(protocols):
        try:
            response = http.get(FILE_URL.format(protocol=name))
            response.raise_for_status()
            frame = parse(name, response.json())
            if frame.height:
                frames.append(frame)
            else:
                problems.append(f"{name}: no documented schedule")
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as e:
            problems.append(f"{name}: {type(e).__name__}: {e}")
        if progress is not None:
            progress(k + 1, len(protocols))
        time.sleep(pause)
    schedules = (pl.concat(frames) if frames else pl.DataFrame(schema=SCHEDULE_SCHEMA)).sort(
        "protocol", "day"
    )
    coins = pl.DataFrame(
        http.get(COINS_URL).raise_for_status().json(),
        schema={"id": pl.Utf8, "symbol": pl.Utf8, "name": pl.Utf8},
    )
    root = Path(data_dir) / "unlocks"
    root.mkdir(parents=True, exist_ok=True)
    schedules.write_parquet(root / "schedules.parquet")
    coins.write_parquet(root / "coins.parquet")
    (root / "ingested.json").write_text(
        json.dumps(
            {
                "at": datetime.now(UTC).isoformat(),
                "protocols": len(protocols),
                "with_schedule": schedules["protocol"].n_unique(),
                "problems": problems,
            },
            indent=2,
        )
    )
    return schedules, coins, problems


def load(data_dir: Path | str) -> tuple[pl.DataFrame, pl.DataFrame]:
    root = Path(data_dir) / "unlocks"
    if not (root / "schedules.parquet").exists():
        raise ValueError("no unlock schedules stored; run kestrel ingest-unlocks first")
    return pl.read_parquet(root / "schedules.parquet"), pl.read_parquet(root / "coins.parquet")


def ticker_map(schedules: pl.DataFrame, coins: pl.DataFrame) -> tuple[dict[str, str], list[str]]:
    """The note's rule: protocol -> <TICKER>USDT by its CoinGecko id's ticker, dropping a
    ticker that two protocols or two CoinGecko ids share. Returns (map, reasons dropped)."""
    ids = dict(zip(coins["id"].to_list(), coins["symbol"].to_list(), strict=True))
    shared = (
        coins.group_by(pl.col("symbol").str.to_uppercase().alias("ticker"))
        .len()
        .filter(pl.col("len") > 1)["ticker"]
        .to_list()
    )
    shared_by_ids = set(shared)
    pairs = schedules.select("protocol", "gecko_id").unique().sort("protocol")
    by_ticker: dict[str, list[str]] = {}
    dropped: list[str] = []
    for protocol, gecko in pairs.iter_rows():
        symbol = ids.get(gecko) if gecko else None
        if symbol is None:
            dropped.append(f"{protocol}: no CoinGecko id {gecko!r}")
            continue
        by_ticker.setdefault(symbol.upper(), []).append(protocol)
    out: dict[str, str] = {}
    for ticker, protocols in sorted(by_ticker.items()):
        if len(protocols) > 1:
            dropped.append(f"{ticker}: shared by protocols {', '.join(protocols)}")
        elif ticker in shared_by_ids:
            dropped.append(f"{ticker}: shared by two or more CoinGecko ids ({protocols[0]})")
        else:
            out[protocols[0]] = f"{ticker}USDT"
    return out, dropped
