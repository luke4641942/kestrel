"""The outside series (kestrel.data.external) and the three non-trend signals built on them
(Coinbase premium, variance premium, net liquidity), with their past-week control. Offline."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest

from kestrel.data import external
from kestrel.studies import weekly_signal as ws
from kestrel.studies.volatile_direction import Sample
from tests.backtest.helpers import bars

DAY = timedelta(days=1)


def series(start: date, values: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": [start + i * DAY for i in range(len(values))], "value": values},
        schema=external.SCHEMA,
    )


def test_above_normal_compares_the_last_7_days_with_the_180_before() -> None:
    day0 = date(2024, 1, 1)
    values = {day0 + timedelta(days=i): 1.0 for i in range(187)}
    t = datetime(2024, 1, 1, tzinfo=UTC) + 186 * DAY  # the 187th day
    assert ws.above_normal([t], values) == [False]  # equal to the median: not above
    for k in range(7):
        values[t.date() - timedelta(days=k)] = 2.0
    assert ws.above_normal([t], values) == [True]
    del values[day0 + timedelta(days=10)]  # one missing day in the 180: unknown, not guessed
    assert ws.above_normal([t], values) == [None]
    assert ws.above_normal([t - DAY], values) == [None]  # too little history


def test_the_coinbase_premium_strips_usdts_own_price() -> None:
    b = bars([100.0, 100.0], step=DAY)
    start = b["open_time"][0].date()
    cb = series(start, [101.0, 99.0])
    usdt = series(start, [1.01, 1.0])
    adjusted = ws.coinbase_premium(b, cb, usdt)
    assert adjusted[start] == pytest.approx(101 / (100 * 1.01) - 1)  # 0: USDT was at 1.01
    assert adjusted[start + DAY] == pytest.approx(-0.01)
    assert ws.coinbase_premium(b, cb, None)[start] == pytest.approx(0.01)
    assert ws.coinbase_premium(b, cb, series(start, [1.0]))  # a missing USDT day is left out
    assert start + DAY not in ws.coinbase_premium(b, cb, series(start, [1.0]))


def test_the_variance_premium_is_implied_less_realised_in_percent() -> None:
    closes = [100.0 * (1.01 if i % 2 else 0.99) for i in range(40)]
    b = bars(closes, step=DAY)
    days = [t.date() for t in b["open_time"].to_list()]
    dvol = series(days[0], [80.0] * 40)
    found = ws.variance_premium(b, dvol)
    assert days[29] not in found  # 30 days of returns need 31 closes
    rv = ws.realised_volatility(b, 30).to_list()[35]
    assert found[days[35]] == pytest.approx(80.0 - 100 * rv)


def test_net_liquidity_is_in_billions_and_known_from_the_saturday_after() -> None:
    wed = date(2024, 1, 3)  # a Wednesday
    weds = [wed + timedelta(weeks=k) for k in range(6)]
    walcl = pl.DataFrame(
        {"date": weds, "value": [8_000_000.0 + 1000 * k for k in range(6)]}, schema=external.SCHEMA
    )
    tga = pl.DataFrame({"date": weds, "value": [700.0] * 6}, schema=external.SCHEMA)
    rrp = pl.DataFrame({"date": weds, "value": [500.0] * 6}, schema=external.SCHEMA)
    weekly = ws.net_liquidity(walcl, tga, rrp)
    assert weekly[wed] == pytest.approx(8000 - 700 - 500)
    last = weds[4]  # 2024-01-31, rising every week
    sunday = datetime(2024, 2, 4, tzinfo=UTC)  # closes Monday: Wed 01-31 known (from Sat)
    thursday = datetime(2024, 2, 1, tzinfo=UTC)  # closes Friday: 01-31 not yet known
    assert ws.liquidity_flags([sunday], weekly) == [True]  # 01-31 vs 01-03
    assert ws.liquidity_flags([thursday], weekly) == [None]  # 01-24 vs 12-27: 12-27 missing
    rrp_gap = rrp.filter(pl.col("date") != last)  # a holiday Wednesday: a gap, not filled
    assert ws.liquidity_flags([sunday], ws.net_liquidity(walcl, tga, rrp_gap)) == [None]


def sample(day: int, up: bool, forward: float) -> Sample:
    return Sample("BTCUSDT", datetime(2024, 1, 7, tzinfo=UTC) + timedelta(weeks=day), up, forward)


def test_the_control_kills_a_spread_that_lives_only_after_rising_weeks() -> None:
    # A signal that is on after rising weeks, which then keep rising: momentum in disguise.
    weeks = 60
    closes: list[float] = []
    price = 100.0
    flags: list[bool | None] = []
    start = datetime(2024, 1, 1, tzinfo=UTC)  # a Monday
    for d in range(weeks * 7):
        week = d // 7
        price *= 1.004 if week % 2 == 0 else 0.997
        closes.append(price)
    b = bars(closes, step=DAY, start=start)
    for t in b["open_time"].to_list():
        flags.append((t - start).days // 7 % 2 == 0)
    momentum = ws.run_signal_study(
        "coinbase-premium", {"BTCUSDT": (b, flags)}, hurdle=0.0, resamples=200
    )
    assert momentum.after_rise is not None
    assert momentum.after_fall is not None
    assert momentum.control_holds is False
    assert momentum.verdict == "KILL"
    old = ws.run_signal_study("stablecoins", {"BTCUSDT": (b, flags)}, hurdle=0.0, resamples=200)
    assert old.after_rise is None  # older studies were not pre-registered with the control
    assert "Control (pre-registered)" in ws.format_signal_study(momentum)
    assert "Control" not in ws.format_signal_study(old)


def test_coinbase_candles_are_fetched_in_300_day_pages() -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["start"])
        first = datetime.fromisoformat(request.url.params["start"].replace("Z", "+00:00"))
        last = datetime.fromisoformat(request.url.params["end"].replace("Z", "+00:00"))
        rows, t = [], last
        while t >= first:
            rows.append([int(t.timestamp()), 1, 2, 1, 1.5, 10])
            t -= DAY
        return httpx.Response(200, json=rows)

    http = httpx.Client(transport=httpx.MockTransport(handle))
    frame = external.fetch_coinbase(
        "BTC-USD", http, date(2020, 1, 1), date(2021, 6, 1), sleep=lambda _: None
    )
    assert len(asked) == 2
    assert frame.height == (date(2021, 6, 1) - date(2020, 1, 1)).days + 1
    assert external.gaps(frame) == []
    with pytest.raises(external.ExternalError, match="unexpected reply"):
        external.fetch_coinbase(
            "BTC-USD",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"message": "no"}))
            ),
            date(2020, 1, 1),
            date(2020, 1, 2),
            sleep=lambda _: None,
        )


def test_dvol_pages_backwards_and_fred_skips_empty_days() -> None:
    def ms(d: date) -> int:
        return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)

    pages = {
        ms(date(2021, 1, 10)): (
            [[ms(date(2021, 1, 9)), 0, 0, 0, 90.0], [ms(date(2021, 1, 10)), 0, 0, 0, 91.0]],
            ms(date(2021, 1, 8)),
        ),
        ms(date(2021, 1, 8)): ([[ms(date(2021, 1, 8)), 0, 0, 0, 89.0]], None),
    }

    def dvol(request: httpx.Request) -> httpx.Response:
        data, nxt = pages[int(request.url.params["end_timestamp"])]
        return httpx.Response(200, json={"result": {"data": data, "continuation": nxt}})

    f = external.fetch_dvol(
        "BTC",
        httpx.Client(transport=httpx.MockTransport(dvol)),
        date(2021, 1, 1),
        date(2021, 1, 10),
    )
    assert f["value"].to_list() == [89.0, 90.0, 91.0]

    body = "observation_date,WALCL\n2024-01-03,7700000\n2024-01-10,.\n2024-01-17,7680000\n"
    fred = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)))
    w = external.fetch_fred("WALCL", fred)
    assert w["date"].to_list() == [date(2024, 1, 3), date(2024, 1, 17)]
    assert external.gaps(w, timedelta(weeks=1)) == [date(2024, 1, 10)]
    with pytest.raises(external.ExternalError, match="unexpected header"):
        external.fetch_fred("WTREGEN", fred)


def test_a_saved_study_is_served_to_the_dashboard(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kestrel.api.app import create_app

    closes = [100.0 * (1.01 if (i // 7) % 2 else 0.995) ** (i % 7) for i in range(7 * 40)]
    b = bars(closes, step=DAY, start=datetime(2024, 1, 1, tzinfo=UTC))
    flags: list[bool | None] = [i % 14 < 7 for i in range(b.height)]
    result = ws.run_signal_study(
        "variance-premium", {"BTCUSDT": (b, flags)}, hurdle=0.002, resamples=100
    )
    ws.save(result, "a-note", "the report", tmp_path, "abc123", beside=result)
    (saved,) = ws.load_all(tmp_path)
    assert (saved["signal"], saved["title"], saved["note"]) == (
        "variance-premium",
        "A high variance premium",
        "a-note",
    )
    assert saved["after_rise"] is not None
    assert saved["beside"]["verdict"] == result.verdict
    (served,) = TestClient(create_app(tmp_path)).get("/api/signal-studies").json()
    assert served["weeks"]["n_on"] == result.weeks.n_up
    assert served["control_holds"] == result.control_holds
    assert served["report"] == "the report"
