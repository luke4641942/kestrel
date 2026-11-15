"""Stablecoin supply from DefiLlama, and the stablecoins signal's timing, offline."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data import stablecoins
from kestrel.studies.weekly_signal import stablecoin_flags
from tests.test_volhold_weekly import daily

START = date(2023, 11, 1)


def payload(values: list[float], start: date = START) -> list[dict[str, object]]:
    return [
        {
            "date": str(int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())),
            "totalCirculatingUSD": {"peggedUSD": v, "peggedEUR": 1.0},
        }
        for d, v in ((start + timedelta(days=i), v) for i, v in enumerate(values))
    ]


def client(body: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)))


def test_fetch_reads_the_usd_pegged_total_and_reports_problems(tmp_path: Path) -> None:
    frame = stablecoins.fetch(client(payload([1.0, 2.0, 3.0])))
    assert frame["supply"].to_list() == [1.0, 2.0, 3.0]
    assert frame["date"][0] == START
    stablecoins.save(frame, tmp_path)
    assert stablecoins.load(tmp_path).equals(frame)
    assert stablecoins.gaps(frame.filter(pl.col("date") != START + timedelta(days=1))) == [
        START + timedelta(days=1)
    ]
    with pytest.raises(stablecoins.StablecoinError, match="duplicate"):
        stablecoins.fetch(client(payload([1.0]) * 2))
    with pytest.raises(stablecoins.StablecoinError, match="no USD-pegged"):
        stablecoins.fetch(client([{"date": "1700000000", "totalCirculatingUSD": {}}]))
    with pytest.raises(stablecoins.StablecoinError, match="ingest-stablecoins"):
        stablecoins.load(tmp_path / "none")


def test_the_signal_uses_only_supply_known_by_the_close() -> None:
    bars = daily([0.0] * 20)  # 2024-01-01 to 2024-01-21
    t = 10  # the close of 2024-01-11: the newest known day is 2024-01-10
    latest = (date(2024, 1, 1) + timedelta(days=t) - START).days - 1
    flat = [100.0] * 80
    grew = flat.copy()
    grew[latest] = 101.0  # known at the close: counts
    today = flat.copy()
    today[latest + 1] = 101.0  # published after the close: must not count
    frame = lambda v: pl.DataFrame(  # noqa: E731
        {"date": [START + timedelta(days=i) for i in range(len(v))], "supply": v},
        schema=stablecoins.SCHEMA,
    )
    assert stablecoin_flags(bars, frame(grew))[t] is True
    assert stablecoin_flags(bars, frame(today))[t] is False
    assert stablecoin_flags(bars, frame([100.0] * 5))[t] is None


def test_the_ingest_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import kestrel.cli as cli

    fake = client(payload([1.0, 2.0]))
    monkeypatch.setattr(cli.httpx, "Client", lambda **_: fake)
    got = CliRunner().invoke(app, ["ingest-stablecoins", "--data-dir", str(tmp_path)])
    assert got.exit_code == 0, got.output
    assert "2 days of stablecoin supply" in got.output


def test_hold_while_stablecoins_grow_decides_on_sundays() -> None:
    from kestrel.strategies.stablecoin_hold import StablecoinHold
    from kestrel.strategy import STRATEGIES, validated_weights

    bars = daily([0.0] * 20)  # 2024-01-01 (Monday) to 2024-01-21; Sundays are days 6, 13, 20
    flags = [1.0] * 7 + [0.0] * 7 + [None] * 7
    w = validated_weights(StablecoinHold(), bars.with_columns(stablecoins_up=pl.Series(flags)))
    assert w.to_list() == [0.0] * 6 + [1.0] * 7 + [0.0] * 8  # in from day 6, out from 13
    with pytest.raises(ValueError, match="stablecoins_up"):
        StablecoinHold().target_weights(bars)
    assert STRATEGIES["stablecoin-hold"].features == ("stablecoins_up",)


def test_the_feature_goes_on_every_coin_of_a_day(tmp_path: Path) -> None:
    from kestrel.data.features import attach_features
    from kestrel.data.schema import Interval

    supply = [100.0 + i for i in range(80)]  # always growing
    stablecoins.save(
        pl.DataFrame(
            {"date": [START + timedelta(days=i) for i in range(80)], "supply": supply},
            schema=stablecoins.SCHEMA,
        ),
        tmp_path,
    )
    bars = daily([0.0] * 20)
    two = pl.concat([bars.with_columns(symbol=pl.lit(s)) for s in ("A", "B")])
    got = attach_features(two, ("stablecoins_up",), "UNIVERSE", Interval.D1, tmp_path)
    assert got.filter(pl.col("open_time") == bars["open_time"][10])["stablecoins_up"].to_list() == [
        1.0,
        1.0,
    ]
