"""Binance delisting announcements: parsing, amendments and the ingest, offline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data import delistings
from kestrel.data.delistings import Article, DelistingError


def ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def body(*paragraphs: str) -> str:
    """An article body in Binance's node-tree format, split across nodes like the real one."""
    children = [
        {
            "node": "element",
            "tag": "p",
            "child": [
                {"node": "text", "text": half} for half in (p[: len(p) // 2], p[len(p) // 2 :])
            ],
        }
        for p in paragraphs
    ]
    return json.dumps({"node": "root", "child": children})


HEGIC = Article(
    "a1",
    "Binance Will Delist HEGIC, RENBTC and SUSD on 2022-04-11",
    datetime(2022, 4, 4, 9, 58, tzinfo=UTC),
)
HEGIC_BODY = body(
    "Based on our most recent reviews, we have decided to delist and cease trading on all "
    "trading pairs for the following tokens at 2022-04-11 09:00 AM (UTC):",
    "Hegic (HEGIC)",
    "renBTC (RENBTC)",
    "sUSD (SUSD)",
    "Deposits of these tokens after 2022-04-12 00:00 AM (UTC) will not be credited.",
)
GFT = Article(
    "a2", "Binance Will Delist GFT, IRIS on 2024-12-10", datetime(2024, 11, 26, 8, 30, tzinfo=UTC)
)
GFT_BODY = body(
    "Binance will delist and cease trading on all spot trading pairs for the following "
    "tokens at 2024-12-10 03:00 (UTC): Gifto (GFT), IRISnet (IRIS)."
)
GFT_UPDATE = Article(
    "a3",
    "Suspension of Gifto (GFT) Deposits and Update on the Delisting Date (2024-12-03)",
    datetime(2024, 11, 29, 14, 10, tzinfo=UTC),
)
GFT_UPDATE_BODY = body(
    "To protect users, deposits for Gifto (GFT) were suspended on 2024-11-29.",
    "Binance Simple Earn will delist GFT after 2025-01-01 06:00 (UTC).",
    "Binance has decided to delist and cease trading on all spot trading pairs for Gifto "
    "(GFT) at 2024-12-03 08:00 (UTC).",
)


def test_only_coin_delistings_and_date_updates_are_kept() -> None:
    assert delistings.kind(HEGIC.title) == "delist"
    assert delistings.kind(GFT_UPDATE.title) == "update"
    vote = (
        "Binance Announced the First Batch of Vote to Delist Results and Will Delist BADGER, "
        "BAL, BETA on 2025-04-16"
    )
    assert delistings.kind(vote) == "delist"
    for other in (
        "Notice of Removal of Spot Trading Pairs - 2026-10-30",
        "Binance Futures Will Delist PTBUSDT and MITOUSDT USDⓈ-M Perpetual Contracts (2026-10-23)",
        "Binance Staking to Delist EZ, TRU, VITE, ANC & MIR",
        "Binance Alpha Will Remove PTB, AA, OWL",
        "Binance Margin Will Delist ARDR/USDT on 2026-03-12",
        "Binance Margin And Loan Will Delist QUICK on 2026-06-12",
    ):
        assert delistings.kind(other) is None, other


def test_titles_name_their_coins() -> None:
    assert delistings.title_coins(HEGIC.title) == ("HEGIC", "RENBTC", "SUSD")
    assert delistings.title_coins("Binance Will Delist COS, D, HIGH, MBOX on 2026-06-19") == (
        "COS",
        "D",
        "HIGH",
        "MBOX",
    )
    assert delistings.title_coins("Binance Will Delist A & B on 2026-01-01") == ("A", "B")
    assert delistings.title_coins(GFT_UPDATE.title) == ("GFT",)
    with pytest.raises(DelistingError, match="unreadable"):
        delistings.title_coins("Binance Will Delist some tokens on 2026-01-01")


def test_the_removal_time_is_the_titles_date_in_utc() -> None:
    parsed = delistings.parse(HEGIC, HEGIC_BODY)
    assert parsed.removal_at == datetime(2022, 4, 11, 9, 0, tzinfo=UTC)
    assert parsed.coins == ("HEGIC", "RENBTC", "SUSD")
    assert not parsed.amendment
    # The update names the deposit and Earn dates too; only the title's date counts.
    update = delistings.parse(GFT_UPDATE, GFT_UPDATE_BODY)
    assert update.removal_at == datetime(2024, 12, 3, 8, 0, tzinfo=UTC)
    assert update.amendment
    assert delistings.removal_time("at 2022-01-02 12:30 PM (UTC)", "2022-01-02").hour == 12
    assert delistings.removal_time("at 2022-01-02 12:30 AM (UTC)", "2022-01-02").hour == 0
    assert delistings.removal_time("at 2022-01-02 01:30 PM (UTC)", "2022-01-02").hour == 13


def test_unreadable_articles_are_reported_not_guessed() -> None:
    with pytest.raises(DelistingError, match="no 2022-04-11"):
        delistings.parse(HEGIC, body("Trading in HEGIC, RENBTC and SUSD stops soon."))
    with pytest.raises(DelistingError, match="SUSD"):
        delistings.parse(HEGIC, body("HEGIC and RENBTC stop at 2022-04-11 09:00 (UTC)."))
    early = Article(HEGIC.code, HEGIC.title, datetime(2022, 4, 12, tzinfo=UTC))
    with pytest.raises(DelistingError, match="before the announcement"):
        delistings.parse(early, HEGIC_BODY)


def test_amendments_move_the_removal_and_stay_visible() -> None:
    parsed = [
        delistings.parse(GFT_UPDATE, GFT_UPDATE_BODY),
        delistings.parse(GFT, GFT_BODY),
        delistings.parse(HEGIC, HEGIC_BODY),
    ]
    frame, problems = delistings.events(parsed)
    assert problems == []
    assert frame["coin"].to_list() == ["HEGIC", "RENBTC", "SUSD", "GFT", "IRIS"]
    gft = frame.row(by_predicate=pl.col("coin") == "GFT", named=True)
    assert gft["removal_at"] == datetime(2024, 12, 3, 8, 0, tzinfo=UTC)
    assert gft["announced_at"] == GFT.published_at
    assert gft["amended_by"] == "a3"
    iris = frame.row(by_predicate=pl.col("coin") == "IRIS", named=True)
    assert iris["removal_at"] == datetime(2024, 12, 10, 3, 0, tzinfo=UTC)
    assert iris["amended_by"] is None


def test_repeats_and_orphan_updates_are_reported() -> None:
    again = Article("a9", HEGIC.title, datetime(2022, 4, 5, tzinfo=UTC))
    frame, problems = delistings.events(
        [
            delistings.parse(HEGIC, HEGIC_BODY),
            delistings.parse(again, HEGIC_BODY),
            delistings.parse(GFT_UPDATE, GFT_UPDATE_BODY),
        ]
    )
    assert frame.height == 3
    assert frame["announced_at"].unique().to_list() == [HEGIC.published_at]
    assert any("announced again" in p for p in problems)
    assert any("GFT: date update with no earlier" in p for p in problems)


class Feed:
    """A fake announcements feed: the catalog in pages, and each article's body."""

    def __init__(self, articles: dict[Article, str], total: int | None = None) -> None:
        self.articles = articles
        self.total = len(articles) if total is None else total
        self.body_requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == delistings.USER_AGENT
        params = request.url.params
        if request.url.path.endswith("/list/query"):
            assert params["catalogId"] == str(delistings.CATALOG)
            size, page = int(params["pageSize"]), int(params["pageNo"])
            rows = list(self.articles)[(page - 1) * size : page * size]
            listed = [
                {"code": a.code, "title": a.title, "releaseDate": ms(a.published_at)} for a in rows
            ]
            catalog = {"catalogId": 161, "total": self.total, "articles": listed}
            return httpx.Response(200, json={"data": {"catalogs": [catalog]}})
        code = params["articleCode"]
        self.body_requests.append(code)
        found = next(b for a, b in self.articles.items() if a.code == code)
        return httpx.Response(200, json={"data": {"body": found}})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


NOISE = Article(
    "n1",
    "Notice of Removal of Spot Trading Pairs - 2026-10-30",
    datetime(2026, 10, 28, tzinfo=UTC),
)


def test_ingest_pages_the_catalog_saves_events_and_keeps_bodies(tmp_path: Path) -> None:
    feed = Feed(
        {GFT_UPDATE: GFT_UPDATE_BODY, NOISE: body("pairs"), HEGIC: HEGIC_BODY, GFT: GFT_BODY}
    )
    with feed.client() as http:
        listed = delistings.list_articles(http, page_size=3, pause=0)
        assert [a.code for a in listed] == ["a1", "a2", "a3", "n1"]  # oldest first
        frame, problems = delistings.ingest(http, tmp_path, pause=0)
    assert problems == []
    assert sorted(feed.body_requests) == ["a1", "a2", "a3"]  # the noise article isn't fetched
    assert delistings.load(tmp_path).equals(frame)
    assert (tmp_path / "delistings" / "articles" / "a1.json").exists()
    with feed.client() as http:  # a rerun reads kept bodies instead of fetching them again
        delistings.ingest(http, tmp_path, pause=0)
    assert len(feed.body_requests) == 3


def test_a_short_listing_is_an_error() -> None:
    short = Feed({HEGIC: HEGIC_BODY}, total=2).client()
    with short as http, pytest.raises(DelistingError, match="says 2 articles but listed 1"):
        delistings.list_articles(http, pause=0)
    with pytest.raises(DelistingError, match="ingest-delistings"):
        delistings.load(Path("/nonexistent"))


def test_ingest_reports_an_unreadable_article_and_carries_on(tmp_path: Path) -> None:
    feed = Feed({HEGIC: body("no time here: HEGIC RENBTC SUSD"), GFT: GFT_BODY})
    with feed.client() as http:
        frame, problems = delistings.ingest(http, tmp_path, pause=0)
    assert frame["coin"].to_list() == ["GFT", "IRIS"]
    assert len(problems) == 1
    assert "no 2022-04-11" in problems[0]


def test_the_command_summarises_and_lists_problems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = Feed({HEGIC: HEGIC_BODY, GFT: GFT_BODY, GFT_UPDATE: GFT_UPDATE_BODY})
    real = httpx.Client

    def offline(*_: object, **__: object) -> httpx.Client:
        return real(transport=httpx.MockTransport(feed))

    monkeypatch.setattr("kestrel.cli.httpx.Client", offline)
    monkeypatch.setattr("kestrel.data.delistings.time.sleep", lambda _: None)
    result = CliRunner().invoke(app, ["ingest-delistings", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "5 coins in 2 announcements, 2022-04-04 to 2024-11-26" in result.output
    assert "1 removal dates amended" in result.output


def test_too_many_requests_backs_off_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr("kestrel.data.delistings.time.sleep", waits.append)
    feed = Feed({HEGIC: HEGIC_BODY})
    replies = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(429)])

    def busy(request: httpx.Request) -> httpx.Response:
        return next(replies, None) or feed(request)

    with httpx.Client(transport=httpx.MockTransport(busy)) as http:
        assert delistings.fetch_body("a1", http) == HEGIC_BODY
    assert waits == [7.0, 60.0]  # Retry-After first, then 30 s doubling
    always = httpx.MockTransport(lambda _: httpx.Response(429))
    with httpx.Client(transport=always) as http, pytest.raises(httpx.HTTPStatusError):
        delistings.fetch_body("a1", http)
