"""The hypothetical delisting short on hand-checkable perps: entry, exit, funding,
liquidation, verdicts, the perp download and the command, all offline."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data import delistings, funding
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.research import SECTIONS, ResearchStore
from kestrel.studies.delisting import CoinPath
from kestrel.studies.delisting_short import (
    PERP_TAKER_FEE,
    PERP_VENUE,
    ShortPath,
    format_short_study,
    ingest_perps,
    run_short_study,
    short_path,
)
from tests.backtest.helpers import bars
from tests.conftest import checksum_for, make_rows, to_zip
from tests.data.test_funding import archive
from tests.test_delisting_study import ANNOUNCED, REMOVED, START, crash_then_rebound, daily

REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")
SPOT_FEE = 7.5e-4
DAY0 = ANNOUNCED.date()  # 2024-03-01
DAY1 = DAY0 + timedelta(days=1)
REMOVAL = REMOVED.date()  # 2024-03-11


def spot(coin: str = "ABC", article: str = "a1", announced: date = DAY0) -> CoinPath:
    half_spread = 15e-4
    return CoinPath(
        coin, article, announced, REMOVAL, 0.0, -0.5, -0.4, 0.0, -0.5, -0.4,
        2 * (SPOT_FEE + half_spread),
    )  # fmt: skip


def perp(opens: list[float], highs: list[float] | None = None, volume: float = 1e6) -> pl.DataFrame:
    """Daily perp bars from day 1 (2024-03-02), one per value."""
    closes = [*opens[1:], opens[-1]]
    return bars(
        opens,
        closes=closes,
        highs=highs or [max(o, c) for o, c in zip(opens, closes, strict=True)],
        start=datetime(2024, 3, 2, tzinfo=UTC),
        step=timedelta(days=1),
        quote_volume=volume,
    )


def rates(*pairs: tuple[datetime, float]) -> pl.DataFrame:
    return funding.parse_archive(archive([(t, 8, r) for t, r in pairs]))


def at(d: date, hour: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=UTC)


FALLING = [10.0, 9.0, 8.0, 7.0, 6.0, 6.0, 6.0, 6.0, 6.0, 6.0]  # days 1..10 (2024-03-02..11)


def test_the_short_enters_at_day_1_and_exits_at_the_removal_days_open() -> None:
    funded = rates(
        (at(DAY1), 0.5),  # at entry, not after it: not counted
        (at(DAY1, 8), 0.01),
        (at(DAY1, 16), -0.004),
        (at(REMOVAL), 0.002),  # at the exit: counted
        (at(REMOVAL, 8), 0.5),  # after the exit: not counted
    )
    p = short_path(spot(), "ABCUSDT", perp(FALLING), funded, 10.5, spot_fee=SPOT_FEE)
    assert isinstance(p, ShortPath)
    assert math.isclose(p.short_return, (10 - 6) / 10)  # day R's open is 6
    assert math.isclose(p.funding, 0.01 - 0.004 + 0.002)
    assert p.discount is not None
    assert math.isclose(p.discount, 10 / 10.5 - 1)
    assert math.isclose(p.round_trip, 2 * (PERP_TAKER_FEE + 15e-4))
    assert p.worst_rise == 0.0  # it only fell
    assert not p.liquidated(2)
    assert math.isclose(p.net(2), 0.4 + 0.008)
    assert p.flag is None


def test_a_squeeze_liquidates_at_2x_before_1x() -> None:
    highs = [10.0, 14.6, *[x for x in FALLING[2:]]]  # +46% on day 2
    p = short_path(spot(), "ABCUSDT", perp(FALLING, highs), rates(), None, spot_fee=SPOT_FEE)
    assert isinstance(p, ShortPath)
    assert math.isclose(p.worst_rise, 0.46)
    assert p.liquidated(2)
    assert not p.liquidated(1)
    assert math.isclose(p.net(2), -0.45)  # the loss, whatever the price did after
    assert math.isclose(p.net(1), 0.4)
    # The exit day's own high doesn't count: the short is closed at its open.
    late = [*FALLING[:-1], 6.0]
    p = short_path(
        spot(), "ABCUSDT", perp(FALLING, [*late[:-1], 99.0]), rates(), None, spot_fee=SPOT_FEE
    )
    assert isinstance(p, ShortPath)
    assert not p.liquidated(2)


def test_a_perp_quoted_per_thousand_is_compared_with_spot_per_coin() -> None:
    thousand = perp([x * 1000 for x in FALLING])
    p = short_path(spot(), "1000ABCUSDT", thousand, rates(), 10.0, spot_fee=SPOT_FEE)
    assert isinstance(p, ShortPath)
    assert p.discount is not None
    assert math.isclose(p.discount, 0.0, abs_tol=1e-12)
    assert math.isclose(p.short_return, 0.4)


def test_a_perp_that_didnt_trade_doesnt_count() -> None:
    dead = perp(FALLING, volume=0.0)
    assert "didn't trade on day 1" in str(
        short_path(spot(), "ABCUSDT", dead, None, None, spot_fee=0)
    )
    later = perp([1.0] * 10).filter(pl.col("open_time").dt.date() > DAY1)
    assert "didn't trade on day 1" in str(short_path(spot(), "X", later, None, None, spot_fee=0))
    quiet_exit = perp(FALLING).with_columns(
        quote_volume=pl.when(pl.col("open_time").dt.date() == REMOVAL).then(0.0).otherwise(1e6)
    )
    assert "exit day" in str(short_path(spot(), "X", quiet_exit, None, None, spot_fee=0))


def test_bars_stopping_early_exit_at_the_last_close_and_missing_funding_is_flagged() -> None:
    short = perp(FALLING[:5])  # days 1..5; closes [9, 8, 7, 6, 6]
    p = short_path(spot(), "ABCUSDT", short, None, None, spot_fee=SPOT_FEE)
    assert isinstance(p, ShortPath)
    assert math.isclose(p.short_return, (10 - 6) / 10)
    assert p.flag is not None
    assert "2024-03-06" in p.flag
    assert "no funding history" in p.flag


def sp(article: str, ret: float, day: int, worst: float = 0.0) -> ShortPath:
    return ShortPath(
        "X", article, START.date() + timedelta(days=day), "XUSDT", ret, 0.0, worst, None, 0.004
    )


def test_the_verdict_needs_ten_announcements_and_double_costs() -> None:
    def verdict(returns: list[float]) -> str:
        return run_short_study([sp(f"e{i}", r, i) for i, r in enumerate(returns)], []).verdict

    assert verdict([0.3] * 9) == "INSUFFICIENT"
    assert verdict([0.3, 0.2, 0.4, 0.25, 0.35, 0.3, 0.2, 0.4, 0.25, 0.35]) == "PROCEED"
    assert verdict([0.007] * 10) == "KILL"  # below twice the 0.4% round trip
    squeezed = [sp(f"e{i}", 0.3, i, worst=0.5) for i in range(10)]  # all liquidated at 2x
    assert run_short_study(squeezed, []).verdict == "KILL"
    assert run_short_study([], ["X: none"]).verdict == "INSUFFICIENT"


def test_the_report_lists_events_information_and_problems() -> None:
    paths = [sp("a", 0.3, 0), sp("a", 0.1, 0, worst=0.6), sp("b", 0.2, 3)]
    text = format_short_study(run_short_study(paths, ["Q: no perp bars stored for its window"]))
    assert "3 coins with a traded perp in 2 announcements (10 needed); 1 coins without" in text
    assert "liquidated: 1 of 3 at 2x, 0 at 1x" in text
    assert "Verdict: INSUFFICIENT" in text
    assert "Q: no perp bars stored" in text


def um_feed(served: dict[str, bytes]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for name, payload in served.items():
            if url.endswith(name):
                return httpx.Response(200, content=payload)
            if url.endswith(name + ".CHECKSUM"):
                return httpx.Response(200, text=checksum_for(payload, name))
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_ingest_fetches_only_the_windows_months_and_tries_the_thousand_name(
    tmp_path: Path,
) -> None:
    month = to_zip(make_rows(datetime(2024, 3, 1, tzinfo=UTC), 31, timedelta(days=1)))
    funded = archive([(at(DAY1, 8), 8, 0.001)])
    served = {
        "futures/um/monthly/klines/1000ABCUSDT/1d/1000ABCUSDT-1d-2024-03.zip": month,
        "1000ABCUSDT/1000ABCUSDT-fundingRate-2024-03.zip": funded,
    }
    seen: list[tuple[str, str]] = []
    with um_feed(served) as http:
        found = ingest_perps(
            [spot("ABC"), spot("NOPE")], str(tmp_path), http, lambda c, s: seen.append((c, s))
        )
    assert found == {"ABC": "1000ABCUSDT", "NOPE": None}
    assert seen == [("ABC", "1000ABCUSDT"), ("NOPE", "no perp")]
    stored = ParquetStore(tmp_path, venue=PERP_VENUE).read("1000ABCUSDT", Interval.D1)
    assert stored.height == 31
    assert ParquetStore(tmp_path).read("1000ABCUSDT", Interval.D1).is_empty()  # not spot
    assert funding.load(tmp_path, "1000ABCUSDT")["rate"].to_list() == [0.001]


def test_the_command_runs_the_frozen_note_on_the_spot_studys_coins(tmp_path: Path) -> None:
    data, research = tmp_path / "data", tmp_path / "research"
    spot_store = ParquetStore(data)
    spot_store.write("ABCUSDT", Interval.D1, daily(crash_then_rebound()))
    spot_store.write("DEFUSDT", Interval.D1, daily(crash_then_rebound()))
    spot_store.write("BTCUSDT", Interval.D1, daily([100.0] * 80))
    ParquetStore(data, venue=PERP_VENUE).write("ABCUSDT", Interval.D1, perp(FALLING))
    funding.save(funding.history_from("ABCUSDT", [rates((at(DAY1, 8), 0.01))], []), data)
    delistings.save(
        pl.DataFrame(
            [
                ("ABC", ANNOUNCED, REMOVED, "a1", None, "t"),
                ("DEF", ANNOUNCED, REMOVED, "a1", None, "t"),  # spot only, no perp
            ],
            schema=delistings.SCHEMA,
            orient="row",
        ),
        data,
    )
    store = ResearchStore(research)
    slug = store.create("Short test").slug
    store.edit(slug, 1, dict.fromkeys(SECTIONS, "x"), "study:delisting-short")
    store.freeze(slug, 1)
    args = ["delisting-short-study", "--research", slug, "--research-dir", str(research)]
    result = CliRunner().invoke(app, [*args, "--data-dir", str(data), "--config", REPO_CONFIG])
    assert result.exit_code == 0, result.output
    assert "1 coins with a traded perp in 1 announcements" in result.output
    assert "DEF: no perp bars stored for its window" in result.output
    assert "Verdict: INSUFFICIENT" in result.output
    assert ResearchStore(research).get(slug).status == "backtested"
    wrong = store.create("Other").slug
    store.edit(wrong, 1, dict.fromkeys(SECTIONS, "x"), "study:delistings")
    store.freeze(wrong, 1)
    refused = CliRunner().invoke(
        app, ["delisting-short-study", "--research", wrong, "--research-dir", str(research)]
    )
    assert refused.exit_code == 1
    assert "not study:delisting-short" in refused.output
