"""Token unlock schedules: parsing DefiLlama's files and the note's ticker rule, offline."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from kestrel.data import unlocks

DAY1 = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp())
DAY2 = DAY1 + 86_400


def doc(gecko: str) -> dict[str, Any]:
    return {
        "gecko_id": gecko,
        "categories": {"insiders": ["Team"], "publicSale": ["Sale"]},
        "documentedData": {"data": [
            {"label": "Team", "data": [{"timestamp": DAY1, "unlocked": 0},
                                       {"timestamp": DAY2, "unlocked": 50}]},
            {"label": "Sale", "data": [{"timestamp": DAY1, "unlocked": 100},
                                       {"timestamp": DAY2, "unlocked": 100}]},
        ]},
    }  # fmt: skip


def test_a_schedule_adds_up_its_sections_and_keeps_the_insiders_apart() -> None:
    frame = unlocks.parse("arb", doc("arbitrum"))
    assert frame.select("day", "unlocked", "insiders").rows() == [
        (date(2023, 1, 1), 100.0, 0.0),
        (date(2023, 1, 2), 150.0, 50.0),
    ]
    assert frame["gecko_id"].unique().to_list() == ["arbitrum"]
    assert unlocks.parse("empty", {"gecko_id": "x"}).height == 0


def test_ingest_writes_schedules_and_coins_and_reports_bad_files(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == unlocks.LIST_URL:
            return httpx.Response(200, json=["arb", "broken", "empty"])
        if url.endswith("/arb"):
            return httpx.Response(200, json=doc("arbitrum"))
        if url.endswith("/empty"):
            return httpx.Response(200, json={"gecko_id": "e"})
        if url == unlocks.COINS_URL:
            return httpx.Response(200, json=[{"id": "arbitrum", "symbol": "arb", "name": "A"}])
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    schedules, coins, problems = unlocks.ingest(http, tmp_path, pause=0)
    assert schedules["protocol"].unique().to_list() == ["arb"]
    assert coins.height == 1
    assert [p.split(":")[0] for p in problems] == ["broken", "empty"]
    loaded, _ = unlocks.load(tmp_path)
    assert loaded.equals(schedules)


def test_the_ticker_rule_drops_any_shared_ticker() -> None:
    schedules = pl.DataFrame(
        {"protocol": ["arb", "uni", "uni2", "solo", "lost"],
         "gecko_id": ["arbitrum", "uniswap", "uniswap-x", "solo-id", "missing"]},
    )  # fmt: skip
    coins = pl.DataFrame(
        {"id": ["arbitrum", "arb-copy", "uniswap", "uniswap-x", "solo-id"],
         "symbol": ["arb", "ARB", "uni", "uni", "solo"], "name": ["a", "b", "c", "d", "e"]},
    )  # fmt: skip
    mapped, dropped = unlocks.ticker_map(schedules, coins)
    assert mapped == {"solo": "SOLOUSDT"}
    assert len(dropped) == 3  # ARB (two CoinGecko ids), UNI (two protocols), lost (no id)
