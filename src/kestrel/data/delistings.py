"""Binance spot delisting announcements: when each coin's delisting was announced, and when
trading stopped.

The source is Binance's public announcements feed, catalog 161 ("Delisting"), which goes
back to 2022-02. A coin delisting is an article titled "Binance Will Delist A, B and C on
YYYY-MM-DD" (the 2025 Vote to Delist batch: "Binance Announced ... and Will Delist ...").
Everything else in the catalog is left out: single trading pairs, and Margin, Futures,
Loan, Alpha, Earn and Staking notices ("Binance Margin Will Delist ..." is margin only),
leveraged tokens. An article titled "... Update on the Delisting Date" moves an
announced coin's removal (Gifto, 2024) and is applied as an amendment, kept visible.

Times. ``announced_at`` is the article's publish time, as Binance records it.
``removal_at`` is the time trading stops, read from the body: the first "YYYY-MM-DD HH:MM
(UTC)" whose date is the one in the title. When no such time is found, or a title coin
isn't named in the body, the article is reported, never guessed.

Prices are not read here. The study that uses these events is pre-registered before any
delisted coin's prices are looked at.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

LIST_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
DETAIL_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
CATALOG = 161
"""Binance's "Delisting" announcements catalog."""
USER_AGENT = "kestrel-research/0.1"
SCHEMA = pl.Schema(
    {
        "coin": pl.String(),
        "announced_at": pl.Datetime("us", "UTC"),
        "removal_at": pl.Datetime("us", "UTC"),
        "article": pl.String(),
        "amended_by": pl.String(),
        "title": pl.String(),
    }
)

_DELIST = re.compile(r"Will Delist (?P<coins>.+?) on (?P<date>\d{4}-\d{2}-\d{2})\s*$")
_SPOT = re.compile(r"^Binance (?:Announced .+ and )?Will Delist ")
"""Spot only: "Binance Margin Will Delist" and "Binance Futures Will Delist" don't match."""
_UPDATE = re.compile(r"Update on the Delisting Date \((?P<date>\d{4}-\d{2}-\d{2})\)")
_TIME = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})\s*(?:at\s*)?(?P<hm>\d{1,2}:\d{2})\s*(?P<ampm>AM|PM)?\s*\(UTC\)"
)
_TICKER = re.compile(r"^[A-Z0-9]{1,12}$")


class DelistingError(ValueError):
    """The feed or an article is malformed: reported, never repaired."""


@dataclass(frozen=True)
class Article:
    code: str
    title: str
    published_at: datetime


@dataclass(frozen=True)
class Parsed:
    """What one article says: these coins stop trading at ``removal_at``."""

    article: Article
    coins: tuple[str, ...]
    removal_at: datetime
    amendment: bool


def kind(title: str) -> str | None:
    """ "delist" for a coin delisting, "update" for a moved date, None for everything else."""
    if _UPDATE.search(title):
        return "update"
    if _DELIST.search(title) and _SPOT.match(title):
        return "delist"
    return None


def title_coins(title: str) -> tuple[str, ...]:
    """The tickers a title names: "A, B and C" for a delisting, "(GFT)" for an update."""
    if kind(title) == "update":
        return tuple(re.findall(r"\(([A-Z0-9]{1,12})\)", title))
    match = _DELIST.search(title)
    if match is None:
        raise DelistingError(f"not a delisting title: {title!r}")
    coins = tuple(c.strip() for c in re.split(r",|\band\b|&", match["coins"]) if c.strip())
    bad = [c for c in coins if not _TICKER.match(c)]
    if bad:
        raise DelistingError(f"unreadable tickers {bad} in {title!r}")
    return coins


def title_date(title: str) -> str:
    match = _UPDATE.search(title) or _DELIST.search(title)
    if match is None:
        raise DelistingError(f"no date in {title!r}")
    return match["date"]


def body_text(body: str) -> str:
    """The plain text of an article body (Binance's JSON node tree)."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("node") == "text":
                parts.append(str(node.get("text", "")))
            for child in node.get("child", []):
                walk(child)

    walk(json.loads(body))
    return "".join(parts)


def removal_time(text: str, date: str) -> datetime:
    """The first "``date`` HH:MM (UTC)" in the text."""
    for match in _TIME.finditer(text):
        if match["date"] != date:
            continue
        hour, minute = (int(x) for x in match["hm"].split(":"))
        if match["ampm"] == "PM" and hour < 12:
            hour += 12
        if match["ampm"] == "AM" and hour == 12:
            hour = 0
        day = datetime.strptime(date, "%Y-%m-%d")
        return day.replace(hour=hour, minute=minute, tzinfo=UTC)
    raise DelistingError(f"no {date} HH:MM (UTC) time in the body")


def parse(article: Article, body: str) -> Parsed:
    """One article's coins and removal time, checked against its body."""
    coins = title_coins(article.title)
    if not coins:
        raise DelistingError(f"no coins in {article.title!r}")
    text = body_text(body)
    missing = [c for c in coins if not re.search(rf"\b{re.escape(c)}\b", text)]
    if missing:
        raise DelistingError(f"{missing} named in the title but not the body: {article.title!r}")
    removal = removal_time(text, title_date(article.title))
    if removal <= article.published_at:
        raise DelistingError(f"removal {removal} is before the announcement: {article.title!r}")
    return Parsed(article, coins, removal, kind(article.title) == "update")


def _get(http: httpx.Client, url: str, params: dict[str, Any], retries: int = 4) -> httpx.Response:
    """GET with a polite back-off: on 429 (too many requests) wait and try again."""
    for attempt in range(retries + 1):
        response = http.get(url, params=params, headers={"User-Agent": USER_AGENT})
        if response.status_code != 429 or attempt == retries:
            response.raise_for_status()
            return response
        wait = response.headers.get("Retry-After")
        time.sleep(float(wait) if wait and wait.isdigit() else 30.0 * 2**attempt)
    raise AssertionError("unreachable")


def list_articles(http: httpx.Client, page_size: int = 50, pause: float = 0.5) -> list[Article]:
    """Every article in the Delisting catalog, oldest first."""
    articles: list[Article] = []
    total: int | None = None
    page = 1
    while True:
        response = _get(
            http,
            LIST_URL,
            {"type": 1, "catalogId": CATALOG, "pageNo": page, "pageSize": page_size},
        )
        catalogs = response.json()["data"]["catalogs"]
        rows = catalogs[0]["articles"] if catalogs else []
        if catalogs and total is None:
            total = int(catalogs[0]["total"])
        if not rows:
            break
        articles += [
            Article(
                str(r["code"]),
                str(r["title"]).strip(),
                datetime.fromtimestamp(int(r["releaseDate"]) / 1000, UTC),
            )
            for r in rows
        ]
        page += 1
        time.sleep(pause)
    if total is not None and len(articles) != total:
        raise DelistingError(f"the feed says {total} articles but listed {len(articles)}")
    if len({a.code for a in articles}) != len(articles):
        raise DelistingError("the feed listed an article twice")
    return sorted(articles, key=lambda a: a.published_at)


def fetch_body(code: str, http: httpx.Client) -> str:
    response = _get(http, DETAIL_URL, {"articleCode": code})
    return str(response.json()["data"]["body"])


def events(parsed: list[Parsed]) -> tuple[pl.DataFrame, list[str]]:
    """One row per delisted coin, amendments applied, and the problems found.

    An amendment moves the removal of a coin announced earlier and is recorded in
    ``amended_by``. A coin delisted by two announcements, or an amendment with no earlier
    announcement, is reported and left as it is.
    """
    problems: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    for p in sorted(parsed, key=lambda p: p.article.published_at):
        for coin in p.coins:
            if p.amendment:
                if coin not in rows:
                    problems.append(f"{coin}: date update with no earlier announcement")
                    continue
                rows[coin]["removal_at"] = p.removal_at
                rows[coin]["amended_by"] = p.article.code
                continue
            if coin in rows:
                problems.append(
                    f"{coin}: announced again on {p.article.published_at:%Y-%m-%d}; "
                    "the first announcement is kept"
                )
                continue
            rows[coin] = {
                "coin": coin,
                "announced_at": p.article.published_at,
                "removal_at": p.removal_at,
                "article": p.article.code,
                "amended_by": None,
                "title": p.article.title,
            }
    frame = pl.DataFrame(list(rows.values()), schema=SCHEMA).sort("announced_at", "coin")
    return frame, problems


def directory(data_dir: Path | str) -> Path:
    return Path(data_dir) / "delistings"


def ingest(
    http: httpx.Client, data_dir: Path | str, pause: float = 1.5
) -> tuple[pl.DataFrame, list[str]]:
    """Fetch the catalog, parse every coin delisting and save the events.

    Article bodies are kept as fetched (``articles/<code>.json``) so a parse can be
    checked against its source; one already on disk is not fetched again.
    """
    root = directory(data_dir)
    (root / "articles").mkdir(parents=True, exist_ok=True)
    parsed: list[Parsed] = []
    problems: list[str] = []
    for article in list_articles(http, pause=pause):
        if kind(article.title) is None:
            continue
        cached = root / "articles" / f"{article.code}.json"
        if cached.exists():
            body = json.loads(cached.read_text())["body"]
        else:
            body = fetch_body(article.code, http)
            cached.write_text(json.dumps({"title": article.title, "body": body}))
            time.sleep(pause)
        try:
            parsed.append(parse(article, body))
        except DelistingError as error:
            problems.append(str(error))
    frame, more = events(parsed)
    save(frame, data_dir)
    return frame, problems + more


def save(frame: pl.DataFrame, data_dir: Path | str) -> Path:
    target = directory(data_dir) / "events.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    frame.write_parquet(tmp)
    tmp.replace(target)
    return target


def load(data_dir: Path | str) -> pl.DataFrame:
    target = directory(data_dir) / "events.parquet"
    if not target.exists():
        raise DelistingError(f"no delisting events at {target}; run `kestrel ingest-delistings`")
    return pl.read_parquet(target)
