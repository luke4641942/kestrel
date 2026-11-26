"""Open interest from Binance's archive and the forced-selling study (note
do-falls-with-forced-perp-selling-bounce-more), all on synthetic data."""

from __future__ import annotations

import io
import math
import random
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest

from kestrel.data import open_interest as oi
from kestrel.studies import oi_flush
from kestrel.studies.oi_flush import Day, coin_days, format_oi_flush, run_oi_flush

HOLDOUT = datetime(2025, 11, 1, tzinfo=UTC)


def day_zip(perp: str, day: date, values: list[float] | None = None) -> bytes:
    """One archive file: 288 five-minute rows from 00:00."""
    start = datetime(day.year, day.month, day.day)
    rows = ["create_time,symbol,sum_open_interest,sum_open_interest_value"]
    for k in range(288):
        t = start + timedelta(minutes=5 * k)
        v = values[k] if values else 1000.0 + k
        rows.append(f"{t:%Y-%m-%d %H:%M:%S},{perp},{v},{v * 30000}")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(f"{perp}-metrics-{day}.csv", "\n".join(rows))
    return out.getvalue()


def test_a_close_is_the_latest_row_at_or_before_it_in_its_last_hour() -> None:
    d1, d2 = date(2023, 3, 1), date(2023, 3, 2)
    rows = pl.concat(
        [oi.parse_day(day_zip("XUSDT", d1), d1), oi.parse_day(day_zip("XUSDT", d2), d2)]
    )
    assert set(rows["create_time"].dt.hour().to_list()) == {0, 23}
    closes = oi.closes(rows)
    by = dict(zip(closes["date"].to_list(), closes["open_interest"].to_list(), strict=True))
    assert by[d1] == 1000.0  # day 2's 00:00 row: stamped at day 1's close
    assert by[d2] == 1000.0 + 287  # its 23:55 row (day 3's file isn't there)
    assert by[date(2023, 2, 28)] == 1000.0  # day 1's own 00:00 row is the day before's close
    late = rows.filter(pl.col("create_time").dt.hour() == 23).filter(pl.col("file_date") == d2)
    assert oi.closes(late.filter(pl.col("create_time").dt.minute() < 0)).is_empty()
    assert oi.closes(oi.load(Path("/nonexistent"), "XUSDT")).is_empty()


def test_a_perp_maps_to_its_spot_coin() -> None:
    assert oi.spot_symbol("1000SHIBUSDT") == "SHIBUSDT"
    assert oi.spot_symbol("1000000MOGUSDT") == "MOGUSDT"
    assert oi.spot_symbol("BTCUSDT") == "BTCUSDT"
    assert oi.spot_symbol("1INCHUSDT") == "1INCHUSDT"  # a name that starts with a digit


def frames(
    n: int, oi_values: list[float | None], start: date = date(2023, 1, 1)
) -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = random.Random(1)
    closes, price = [], 100.0
    for _ in range(n):
        price *= math.exp(rng.gauss(0, 0.03))
        closes.append(price)
    t0 = datetime(start.year, start.month, start.day, tzinfo=UTC)
    bars = pl.DataFrame(
        {
            "open_time": [t0 + timedelta(days=i) for i in range(n)],
            "open": [closes[i - 1] if i else closes[0] for i in range(n)],
            "close": closes,
        }
    )
    days = [start + timedelta(days=i) for i in range(n)]
    known = [(d, v) for d, v in zip(days, oi_values, strict=True) if v is not None]
    oi_frame = pl.DataFrame(
        {"date": [d for d, _ in known], "open_interest": [v for _, v in known]},
        schema={"date": pl.Date, "open_interest": pl.Float64},
    )
    return bars, oi_frame


def test_coin_days_score_z_and_the_net_return_and_skip_what_they_cant_know() -> None:
    rng = random.Random(2)
    values = [1000.0 * math.exp(rng.gauss(0, 0.02) * i / 50) for i in range(200)]
    values[150] = values[149] * 0.7  # a flush
    bars, frame = frames(200, values)  # type: ignore[arg-type]
    found, missing = coin_days("XUSDT", bars, frame, 0.00075, HOLDOUT, [0.001] * 200)
    assert missing == 0
    days = {d.day: d for d in found}
    flush = days[date(2023, 1, 1) + timedelta(days=150)]
    assert flush.z < -2
    opens = bars["open"].to_list()
    assert flush.net == pytest.approx(opens[156] / opens[151] * (1 - 2 * (0.00075 + 0.001)) - 1)
    assert min(days) >= date(2023, 1, 1) + timedelta(days=KNOWN_DAYS)
    assert max(days) == date(2023, 1, 1) + timedelta(days=200 - 1 - 6)  # a full 5 days ahead

    gappy = list(values)
    gappy[100] = None  # type: ignore[call-overload]
    bars2, frame2 = frames(200, gappy)  # type: ignore[arg-type]
    found2, missing2 = coin_days("XUSDT", bars2, frame2, 0.00075, HOLDOUT)
    assert missing2 == 1
    skipped = {d.day for d in found} - {d.day for d in found2}
    assert date(2023, 1, 1) + timedelta(days=100) in skipped  # its change can't be known
    assert date(2023, 1, 1) + timedelta(days=101) in skipped

    cut = datetime(2023, 6, 1, tzinfo=UTC)
    assert all(
        d.day + timedelta(days=6) < cut.date() for d in coin_days("XUSDT", bars, frame, 0.0, cut)[0]
    )


KNOWN_DAYS = oi_flush.KNOWN


def synthetic(flush_bonus: float, dates: int = 40) -> dict[str, list[Day]]:
    """Coins whose flush days earn ``flush_bonus`` more than ordinary falls."""
    rng = random.Random(3)
    out: dict[str, list[Day]] = {}
    for c in range(6):
        coin = f"C{c}USDT"
        days = []
        for i in range(400):
            d = date(2022, 3, 1) + timedelta(days=i)
            ret = rng.gauss(-0.005, 0.04)
            is_flush = (
                ret < 0 and i % (400 // dates) == 0 and i < dates * (400 // dates)
            )  # shared dates
            z = -3.0 if is_flush else rng.uniform(-0.4, 2.0)
            net = rng.gauss(0.0, 0.05) + (flush_bonus if is_flush else 0.0)
            days.append(Day(coin, d, ret, z, net))
        out[coin] = days
    return out


def test_flushes_that_bounce_more_proceed_and_ones_that_dont_are_dip_buying() -> None:
    good = run_oi_flush(synthetic(0.08))
    assert good.dates >= 30
    assert good.test1
    assert good.test2
    assert good.verdict == "PROCEED"
    same = run_oi_flush(synthetic(0.0))
    assert same.verdict == "KILL"
    assert "dip-buying under another name" in format_oi_flush(same, 6)
    few = run_oi_flush(synthetic(0.08, dates=10))
    assert not few.test3
    assert few.verdict == "KILL"


def test_btc_eth_and_sol_are_reported_apart() -> None:
    coins = synthetic(0.08)
    coins["BTCUSDT"] = [Day("BTCUSDT", d.day, d.ret, d.z, d.net) for d in coins.pop("C0USDT")]
    r = run_oi_flush(coins)
    assert all(f.day.coin != "BTCUSDT" for f in r.flushes)
    assert r.info
    assert all(f.day.coin == "BTCUSDT" for f in r.info)


def test_the_bootstrap_resamples_whole_dates() -> None:
    one_date = [oi_flush.Flush(Day("A", date(2022, 5, 1), -0.1, -3, 0.0), 0.05, 3)] * 50
    assert oi_flush.date_bootstrap(one_date) is None  # one date is one observation
    assert (
        oi_flush.date_bootstrap(
            [*one_date, oi_flush.Flush(Day("A", date(2022, 6, 1), -0.1, -3, 0.0), -0.05, 3)]
        )
        is not None
    )


LISTING = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>{body}</ListBucketResult>"""


class FakeArchive:
    def __init__(self, days: list[date], broken: set[date] | None = None) -> None:
        self.days, self.broken, self.fetched = days, broken or set(), []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host.startswith("s3"):
            prefix = request.url.params["prefix"]
            if request.url.params.get("delimiter"):
                body = "<CommonPrefixes><Prefix>data/futures/um/daily/metrics/XUSDT/</Prefix></CommonPrefixes>"  # noqa: E501
            else:
                keys = [f"{prefix}XUSDT-metrics-{d}.zip" for d in self.days]
                if request.url.params.get("max-keys") == "1":
                    keys = keys[:1]
                body = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
            return httpx.Response(200, text=LISTING.format(body=body))
        day = date.fromisoformat(request.url.path.rsplit("-metrics-", 1)[1].removesuffix(".zip"))
        self.fetched.append(day)
        if day in self.broken:
            return httpx.Response(404)
        return httpx.Response(200, content=day_zip("XUSDT", day))


def test_ingest_downloads_resumes_and_reports_failures(tmp_path: Path) -> None:
    days = [date(2023, 1, 1) + timedelta(days=i) for i in range(5)]
    fake = FakeArchive(days, broken={days[2]})
    with httpx.Client(transport=httpx.MockTransport(fake.handler)) as http:
        assert oi.perps(http) == ["XUSDT"]
        assert oi.first_day(http, "XUSDT") == days[0]
        got = oi.ingest("XUSDT", http, tmp_path, days[-2], workers=1)
        assert (got.added, got.failed) == (3, [days[2]])
        fake.fetched.clear()
        again = oi.ingest("XUSDT", http, tmp_path, days[-1], workers=1)
        assert sorted(fake.fetched) == [days[2], days[4]]  # only what's missing
        assert again.added == 1
        chosen, _left = oi.universe(http, {"XUSDT"}, set(), listed_by=days[0])
        assert chosen == {"XUSDT": "XUSDT"}
        assert oi.universe(http, {"XUSDT"}, {"XUSDT"})[0] == {}
        assert oi.universe(http, set(), set())[1] == ["XUSDT: no stored spot pair"]
    assert oi.load(tmp_path, "XUSDT")["file_date"].n_unique() == 4


def test_the_event_study_command_runs_once_from_the_frozen_note(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from kestrel.cli import app
    from kestrel.data.schema import Interval
    from kestrel.data.store import ParquetStore
    from kestrel.exploration import SPLIT_FILE, Split, write_split
    from kestrel.research import SECTIONS, ResearchStore
    from tests.backtest.helpers import bars

    repo = Path(__file__).resolve().parents[1]
    (tmp_path / "kestrel.toml").write_text((repo / "kestrel.toml").read_text())
    write_split(
        tmp_path / SPLIT_FILE,
        Split(date(2026, 11, 16), 1, frozenset({"XUSDT"}), frozenset({"LOCKUSDT"})),
    )
    research = tmp_path / "research"
    store = ResearchStore(research)
    s = store.create("Do falls with forced perp selling bounce more")
    store.edit(s.slug, 1, dict.fromkeys(SECTIONS, "x"), "study:oi-flush")
    store.freeze(s.slug, 1)
    data = tmp_path / "data"
    start = datetime(2023, 1, 1, tzinfo=UTC)
    rng = random.Random(5)
    closes = [100 * math.exp(rng.gauss(0, 0.03) * 1) for _ in range(300)]
    for coin in ("XUSDT", "BTCUSDT"):
        ParquetStore(data).write(
            coin, Interval.D1, bars(closes, start=start, step=timedelta(days=1))
        )
    rows = []
    for i in range(300):
        d = date(2023, 1, 1) + timedelta(days=i)
        t = datetime(d.year, d.month, d.day, 23, 55, tzinfo=UTC)
        rows.append({"file_date": d, "create_time": t, "open_interest": 1000.0 + rng.gauss(0, 20)})
    oi._save(pl.DataFrame(rows, schema=oi.SCHEMA), data, "XUSDT")
    args = ["event-study", "oi-flush", "--research-dir", str(research), "--data-dir", str(data),
            "--config", str(tmp_path / "kestrel.toml")]  # fmt: skip
    got = CliRunner().invoke(app, args)
    assert got.exit_code == 0, got.output
    assert "Forced selling" in got.output
    assert "Verdict: " in got.output
    assert (data / "studies" / "oi-flush.json").exists()
    assert ResearchStore(research).get(s.slug).status == "backtested"
