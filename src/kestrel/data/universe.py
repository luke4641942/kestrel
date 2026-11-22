"""The coin universe: every USDT pair Binance's public archive has ever held.

Cross-sectional strategies (ranking many coins against each other) are only honest on a
universe free of survivorship bias: a coin list taken from today's exchange leaves out
everything that collapsed or was delisted, and those are exactly the coins a momentum
strategy might have bought near their peak. Binance's archive keeps the history of
delisted pairs (LUNA, FTT, SRM...), so the universe is read from the archive itself, not
from the live exchange.

Not every pair is a coin. Excluded, with the reason recorded:

- leveraged tokens: a base that is another listed coin plus UP, DOWN, BULL or BEAR
  (BTCUP, ETHBEAR), and Binance's Bitcoin tokens BULL and BEAR. A plain suffix rule would
  also drop real coins such as JUP and SYRUP;
- stablecoins, fiat currencies and gold (PAXG, XAUT): built to hold a fixed price, or to
  track one outside crypto, so there is no crypto momentum to rank;
- tokenized stocks and ETFs (AAPLBUSDT, MSFTBUSDT, EWYBUSDT...), listed from June 2026: the
  pairs Binance's product list tags ``bStocks``. No name rule can find them (ARB, BNB, CKB
  also end in B), so the ingest reads the tags (``ProductTags``). That list holds only
  pairs listed today: an archived pair ending in BUSDT that it doesn't know (a delisted
  stock, or a delisted coin) is reported as ``unclassified``, never silently kept or
  dropped. None of these could reach research runs (all first trade after the 2025-11
  holdout starts) or the exploration split.

Which coins are *eligible* on a given date (enough history, enough volume) is decided
point in time by the strategy, from data known then. This module only says what exists.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

from kestrel.data.binance_vision import BinanceVisionClient, Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore

LISTING_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
PRODUCTS_URL = "https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-products"
TOKENIZED_TAG = "bStocks"
TOKENIZED = "tokenized stock or ETF"
KLINES_PREFIX = "data/spot/monthly/klines/"
QUOTE = "USDT"

LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
BTC_LEVERAGED = frozenset({"BULL", "BEAR"})
NOT_COINS = frozenset(
    {
        # Stablecoins, including bridged and retired ones.
        "BUSD", "USDC", "TUSD", "FDUSD", "DAI", "USDP", "PAX", "SUSD", "USDS", "USDSB",
        "USDSOLD", "USDE", "USDEB", "USD1", "XUSD", "BFUSD", "RLUSD", "RWUSD", "UST", "USTC",
        # Fiat currencies.
        "EUR", "AEUR", "EURI", "GBP", "AUD", "TRY", "BRL",
        # Gold.
        "PAXG", "XAUT",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Exclusion:
    symbol: str
    reason: str


def split_universe(
    symbols: list[str], tokenized: Collection[str] = ()
) -> tuple[list[str], list[Exclusion]]:
    """USDT pairs that are coins, and every other USDT pair with why it was left out.
    ``tokenized``: the pairs Binance tags as tokenized stocks (``ProductTags``)."""
    stocks = set(tokenized)
    pairs = sorted({s for s in symbols if s.endswith(QUOTE) and len(s) > len(QUOTE)})
    bases = {s[: -len(QUOTE)] for s in pairs}
    coins: list[str] = []
    excluded: list[Exclusion] = []
    for symbol in pairs:
        base = symbol[: -len(QUOTE)]
        if base in BTC_LEVERAGED or any(
            base.endswith(sfx) and base[: -len(sfx)] in bases for sfx in LEVERAGED_SUFFIXES
        ):
            excluded.append(Exclusion(symbol, "leveraged token"))
        elif base in NOT_COINS:
            excluded.append(Exclusion(symbol, "stablecoin, fiat or gold"))
        elif symbol in stocks:
            excluded.append(Exclusion(symbol, TOKENIZED))
        else:
            coins.append(symbol)
    return coins, excluded


class ArchiveListing:
    """Reads the archive's directory listing (an S3 bucket). HTTP is injectable so tests
    run offline."""

    def __init__(self, http: httpx.Client | None = None, timeout: float = 60.0) -> None:
        self._http = http or httpx.Client(timeout=timeout, follow_redirects=True)

    def _list(self, prefix: str, pattern: str) -> list[str]:
        found: list[str] = []
        marker = ""
        while True:
            response = self._http.get(
                LISTING_URL, params={"delimiter": "/", "prefix": prefix, "marker": marker}
            )
            response.raise_for_status()
            page = re.findall(pattern, response.text)
            found += page
            truncated = "<IsTruncated>true</IsTruncated>" in response.text
            if not truncated or not page:
                return found
            marker = prefix + page[-1]

    def symbols(self) -> list[str]:
        """Every symbol with monthly klines in the archive, listed or delisted."""
        prefix = re.escape(KLINES_PREFIX)
        return self._list(KLINES_PREFIX, rf"<Prefix>{prefix}([^/<]+)/</Prefix>")

    def months(self, symbol: str, interval: Interval) -> list[Month]:
        """Months the archive has for one symbol and interval."""
        prefix = f"{KLINES_PREFIX}{symbol}/{interval.value}/"
        name = re.escape(f"{symbol}-{interval.value}-")
        found = self._list(prefix, rf"<Key>{re.escape(prefix)}({name}\d{{4}}-\d{{2}}\.zip)</Key>")
        return sorted({Month.parse(f[-11:-4]) for f in found}, key=str)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ArchiveListing:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass(frozen=True)
class Products:
    """Binance's current product list: every pair listed today, and the tokenized ones."""

    listed: frozenset[str]
    tokenized: frozenset[str]

    def unclassified(self, symbols: Collection[str]) -> list[str]:
        """Pairs ending in BUSDT the list doesn't know (delisted): they could be a delisted
        tokenized stock, so they're reported for a look."""
        return sorted(s for s in symbols if s.endswith("B" + QUOTE) and s not in self.listed)


class ProductTags:
    """Reads Binance's public product list (each pair's tags). HTTP is injectable."""

    def __init__(self, http: httpx.Client | None = None, timeout: float = 60.0) -> None:
        self._http = http or httpx.Client(timeout=timeout, follow_redirects=True)

    def products(self) -> Products:
        response = self._http.get(PRODUCTS_URL, params={"includeEtf": "true"})
        response.raise_for_status()
        rows = response.json().get("data") or []
        if not rows:
            raise ValueError("Binance's product list came back empty; can't classify pairs")
        listed = frozenset(str(r["s"]) for r in rows)
        tokenized = frozenset(str(r["s"]) for r in rows if TOKENIZED_TAG in (r.get("tags") or []))
        if not tokenized:
            raise ValueError(
                f"no pair tagged {TOKENIZED_TAG} in Binance's product list: the tag may have "
                "changed; tokenized stocks can't be excluded"
            )
        return Products(listed, tokenized)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ProductTags:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass(frozen=True)
class CoinIngest:
    symbol: str
    archived: int
    """Months the archive has in the requested range."""
    fetched: int
    """Months downloaded now (the rest were already stored)."""


def ingest_universe(
    listing: ArchiveListing,
    client: BinanceVisionClient,
    store: ParquetStore,
    interval: Interval,
    start: Month,
    end: Month,
    *,
    products: Products,
    workers: int = 8,
    on_coin: Callable[[CoinIngest, int, int], None] | None = None,
) -> tuple[list[CoinIngest], list[Exclusion], list[str]]:
    """Store every coin's archived months in ``start..end``, skipping months already
    stored, and record what the universe was. Coins are fetched in parallel; each writes
    only its own directory, so workers never touch the same files. Also returns the
    archived pairs ``products`` can't classify (``Products.unclassified``)."""
    symbols = listing.symbols()
    coins, excluded = split_universe(symbols, products.tokenized)
    unclassified = products.unclassified(coins)
    lo, hi = str(start), str(end)

    def one(symbol: str) -> CoinIngest:
        wanted = [m for m in listing.months(symbol, interval) if lo <= str(m) <= hi]
        have = set(store.months(symbol, interval))
        missing = [m for m in wanted if str(m) not in have]
        frames = [f for m in missing if (f := client.fetch_month(symbol, interval, m)) is not None]
        if frames:
            store.write(symbol, interval, pl.concat(frames))
        return CoinIngest(symbol, len(wanted), len(frames))

    results: list[CoinIngest] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, done in enumerate(pool.map(one, coins), start=1):
            results.append(done)
            if on_coin is not None:
                on_coin(done, i, len(coins))
    _write_manifest(store, interval, start, end, results, excluded, unclassified)
    return results, excluded, unclassified


def manifest_path(store: ParquetStore, interval: Interval) -> Path:
    return store.root / "universe" / f"{interval.value}.json"


def _write_manifest(
    store: ParquetStore,
    interval: Interval,
    start: Month,
    end: Month,
    results: list[CoinIngest],
    excluded: list[Exclusion],
    unclassified: list[str],
) -> None:
    """What the universe was when it was fetched, so a backtest can say what it ranked."""
    path = manifest_path(store, interval)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "interval": interval.value,
        "fetched_at": datetime.now(UTC).isoformat(),
        "source": LISTING_URL,
        "start": str(start),
        "end": str(end),
        "coins": {
            r.symbol: r.archived for r in sorted(results, key=lambda r: r.symbol) if r.archived
        },
        "excluded": [{"symbol": e.symbol, "reason": e.reason} for e in excluded],
        "tokenized_source": PRODUCTS_URL,
        "unclassified": unclassified,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2))
    tmp.replace(path)
