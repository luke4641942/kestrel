"""The Fed-meeting dollar study (note does-the-dollar-fall-against-other-currencies-on) and
the forex data it needs: scheduled statement days, and daily bars ending 00:00 UTC."""

from __future__ import annotations

import math
import random
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data import fx
from kestrel.data.fomc import statement_days, statement_instant
from kestrel.studies import fomc_dollar as fd

NY = ZoneInfo("America/New_York")
HOLDOUT = datetime(2023, 1, 1, tzinfo=UTC)


def test_the_statement_days_are_the_feds_scheduled_meetings() -> None:
    days = statement_days()
    per_year = Counter(d.year for d in days)
    assert per_year[2020] == 7  # March 2020 was cancelled; its replacements were unscheduled
    assert all(per_year[y] == 8 for y in range(2006, 2023) if y != 2020)
    assert all(d.weekday() < 5 for d in days)
    assert date(2008, 12, 16) in days  # known statements
    assert date(2015, 12, 16) in days
    assert date(2013, 5, 1) in days  # "April/May 30-1": the last day
    assert date(2008, 1, 22) not in days  # a conference call (unscheduled cut)
    assert date(2020, 3, 15) not in days  # unscheduled
    assert days == sorted(days)


def test_the_statement_falls_inside_one_bar_either_way() -> None:
    instant = statement_instant(date(2019, 7, 31))
    assert instant == datetime(2019, 7, 31, 18, tzinfo=UTC)  # 14:00 New York in summer
    ny_open = datetime(2019, 7, 30, 17, tzinfo=NY).astimezone(UTC)
    utc_open = datetime(2019, 7, 31, tzinfo=UTC)
    for opened in (ny_open, utc_open):
        assert opened <= instant < opened + timedelta(days=1)


def test_a_leg_pays_its_spread_and_its_financing() -> None:
    bar = {"open": 1.0, "close": 1.01, "bid_open": 0.9999, "ask_open": 1.0001,
           "bid_close": 1.0099, "ask_close": 1.0101}  # fmt: skip
    net, mid = fd.leg_return(bar, "EURUSD")  # long: buy at the ask, sell at the bid
    assert mid == pytest.approx(0.01)
    assert net == pytest.approx(1.0099 / 1.0001 - 1)
    short_net, short_mid = fd.leg_return(bar, "USDJPY")  # short USD: sell bid, buy ask
    assert short_mid == pytest.approx(1 / 1.01 - 1)
    assert short_net == pytest.approx(0.9999 / 1.0101 - 1)
    rate = {"EUR": 0.04, "USD": 0.01, "JPY": 0.0}
    assert fd.financing("EURUSD", rate, 0.01) == pytest.approx((0.04 - 0.01 - 0.01) / 365)
    assert fd.financing("USDJPY", rate, 0.01) == pytest.approx((0.0 - 0.01 - 0.01) / 365)


class FlatRates:
    def known_rate(self, currency: str, at: datetime) -> float:
        return 0.02


def market(bump: float, seed: int = 1, days: int = 4500) -> dict[str, pl.DataFrame]:
    """Daily New York bars for the basket's pairs from 2006-02: a random walk, plus
    ``bump`` for foreign currencies against USD on each statement's bar."""
    start = datetime(2006, 1, 30, 17, tzinfo=NY)
    fomc = {statement_instant(d) for d in statement_days()}
    rnd = random.Random(seed)
    frames = {}
    for pair in {*fd.LEGS, "AUDUSD"}:
        sign = 1 if pair in fd.LONG or pair == "AUDUSD" else -1
        rows, price = [], 1.0
        t = start
        while len(rows) < days:
            if t.weekday() in (4, 5):  # Friday and Saturday opens: no trading day
                t += timedelta(days=1)
                continue
            opened = t.astimezone(UTC)
            hit = any(opened <= f < opened + timedelta(days=1) for f in fomc)
            move = rnd.gauss(0, 0.004) + (sign * bump if hit else 0.0)
            close = price * math.exp(move)
            half = 0.00005 * price
            rows.append({"open_time": opened, "open": price, "close": close,
                         "high": max(price, close), "low": min(price, close),
                         "bid_open": price - half, "ask_open": price + half,
                         "bid_close": close - half, "ask_close": close + half})  # fmt: skip
            price, t = close, t + timedelta(days=1)
        frames[pair] = pl.DataFrame(rows).with_columns(pl.col("open_time").dt.cast_time_unit("us"))
    return frames


def test_a_real_fed_day_effect_proceeds_and_none_is_killed() -> None:
    days = statement_days()
    strong = fd.run_fomc(market(0.004), days, FlatRates(), 0.01, "ny", HOLDOUT, placebos=200)
    assert len(strong.days) > 40
    assert strong.test1
    assert strong.test2
    assert strong.test3
    assert strong.test4
    assert fd.verdict(strong, strong) == "PROCEED"
    none = fd.run_fomc(market(0.0), days, FlatRates(), 0.01, "ny", HOLDOUT, placebos=200)
    assert not none.test1
    assert fd.verdict(none, strong) == "KILL"
    report = fd.format_fomc(strong, none)
    assert "Verdict: KILL" in report  # the UTC run failing kills it too
    assert "Legs positive" in report


def test_statements_without_a_bar_are_reported_and_the_holdout_is_never_read() -> None:
    frames = market(0.0)
    first = statement_instant(statement_days()[1])
    gone = {p: f.filter(~((pl.col("open_time") <= first)
                          & (pl.col("open_time") + timedelta(days=1) > first)))
            for p, f in frames.items()}  # fmt: skip
    r = fd.run_fomc(gone, statement_days(), FlatRates(), 0.01, "ny", HOLDOUT, placebos=20)
    assert statement_days()[1] in r.missing
    early = fd.run_fomc(frames, statement_days(), FlatRates(), 0.01, "ny",
                        datetime(2007, 1, 1, tzinfo=UTC), placebos=20)  # fmt: skip
    assert all(d.day < date(2007, 1, 1) for d in early.days)


def test_utc_bars_are_checked_and_trimmed_as_utc_days() -> None:
    def frame(opens: list[datetime]) -> pl.DataFrame:
        n = len(opens)
        cols = {c: [1.0] * n for c in fx.FX_SCHEMA if c not in ("open_time", "ticks")}
        return pl.DataFrame({"open_time": opens, **cols, "ticks": [100] * n}).with_columns(
            pl.col("open_time").dt.cast_time_unit("us")
        )

    week = [datetime(2019, 7, d, tzinfo=UTC) for d in range(28, 32)]  # Sunday 28 to Wed 31
    report = fx.check(frame(week), "EURUSD", HOLDOUT, "utc")
    assert report.misaligned == 0
    assert report.weekend_candles == 1  # the Sunday
    assert fx.check(frame(week), "EURUSD", HOLDOUT, "ny").misaligned == 4
    kept, dropped = fx.trading_days(frame(week), "utc")
    assert dropped == 1
    assert kept["open_time"].min() == datetime(2019, 7, 29, tzinfo=UTC)


def test_utc_bars_are_stored_apart(tmp_path: Path) -> None:
    assert fx.path(tmp_path, "eurusd", "utc").name == "EURUSD-1d-utc.parquet"
    assert fx.path(tmp_path, "eurusd").name == "EURUSD-1d.parquet"
    with pytest.raises(fx.FxError, match="--day-end utc"):
        fx.load(tmp_path, "EURUSD", "utc")


def test_the_command_needs_its_frozen_note(tmp_path: Path) -> None:
    got = CliRunner().invoke(
        app,
        [
            "fx-study",
            "fomc-dollar",
            "--research-dir",
            str(tmp_path / "r"),
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert got.exit_code == 1
    assert "does-the-dollar-fall-against-other-currencies-on" in got.output
    unknown = CliRunner().invoke(app, ["fx-study", "nothing"])
    assert unknown.exit_code == 1
