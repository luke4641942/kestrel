"""Forex daily candles from OANDA (kestrel.data.fx) and the [fx] config. Offline: HTTP is a
mock transport, and no test sees a real token."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.config import ConfigError, load_config, parse_config
from kestrel.data import fx

HOLDOUT = datetime(2023, 1, 1, tzinfo=UTC)
REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")


def candle(t: str, mid: float = 1.1, half: float = 0.0001, ticks: int = 100) -> dict[str, Any]:
    def side(px: float) -> dict[str, str]:
        return {"o": f"{px}", "h": f"{px + 0.01}", "l": f"{px - 0.01}", "c": f"{px}"}

    return {
        "time": f"{t}.000000000Z",
        "complete": True,
        "volume": ticks,
        "mid": side(mid),
        "bid": side(mid - half),
        "ask": side(mid + half),
    }


# A normal week in northern winter: opens Sunday to Thursday at 22:00 UTC (17:00 New York).
WEEK = [
    "2015-03-01T22:00:00",
    "2015-03-02T22:00:00",
    "2015-03-03T22:00:00",
    "2015-03-04T22:00:00",
    "2015-03-05T22:00:00",
]
# The next week starts after the US clock change: 21:00 UTC is still 17:00 New York.
NEXT = ["2015-03-08T21:00:00", "2015-03-09T21:00:00"]


def test_the_fx_config_is_fixed_and_checked() -> None:
    cfg = load_config(REPO_CONFIG)
    assert cfg.fx is not None
    assert cfg.fx.holdout_start == HOLDOUT
    assert len(cfg.fx.pairs) == 10
    assert "EURUSD" in cfg.fx.pairs
    doc = dict(cfg.document)
    assert parse_config({k: v for k, v in doc.items() if k != "fx"}).fx is None  # optional
    with pytest.raises(ConfigError, match="six-letter pairs"):
        parse_config({**doc, "fx": {**doc["fx"], "pairs": ["EUR/USD"]}})
    with pytest.raises(ConfigError, match=r"\[fx\]: unknown key"):
        parse_config({**doc, "fx": {**doc["fx"], "leverage": 30}})


def test_names_pips_and_the_token() -> None:
    assert fx.instrument("eurusd") == "EUR_USD"
    assert (fx.pip("USDJPY"), fx.pip("EURUSD")) == (0.01, 0.0001)
    with pytest.raises(fx.FxError, match="six letters"):
        fx.instrument("EUR")


def test_the_token_comes_from_the_environment_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(fx.TOKEN_ENV, raising=False)
    with pytest.raises(fx.FxError, match=fx.TOKEN_ENV):
        fx.token()
    monkeypatch.setenv(fx.TOKEN_ENV, " abc ")
    assert fx.token() == "abc"


def test_parsing_keeps_complete_candles_with_bid_ask_and_mid() -> None:
    forming = {**candle("2015-03-06T22:00:00"), "complete": False}
    f = fx.parse([candle(WEEK[0], mid=1.2, half=0.0002, ticks=7), forming])
    assert f.height == 1  # the forming candle is left out
    row = f.row(0, named=True)
    assert row["open_time"] == datetime(2015, 3, 1, 22, tzinfo=UTC)
    assert row["close"] == pytest.approx(1.2)
    assert (row["bid_close"], row["ask_close"]) == (pytest.approx(1.1998), pytest.approx(1.2002))
    assert row["ticks"] == 7
    with pytest.raises(fx.FxError, match="unreadable candle"):
        fx.parse([{"time": WEEK[0], "complete": True}])


def test_fetching_pages_and_sends_the_token_only_in_the_header() -> None:
    days = [datetime(2010, 1, 3, 22, tzinfo=UTC) + timedelta(days=i) for i in range(7)]
    seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        assert request.headers["Authorization"] == "Bearer secret"
        assert "secret" not in str(request.url)
        since = datetime.fromisoformat(params["from"].replace("Z", "+00:00"))
        after = [d for d in days if d > since or (params["includeFirst"] == "true" and d >= since)]
        page = after[: int(params["count"])]
        return httpx.Response(
            200, json={"candles": [candle(d.strftime("%Y-%m-%dT%H:%M:%S")) for d in page]}
        )

    http = httpx.Client(transport=httpx.MockTransport(handle))
    pages = fx.PAGE
    try:
        fx.PAGE = 3  # type: ignore[misc]
        f = fx.fetch_daily("EURUSD", http, "secret", datetime(2010, 1, 1, tzinfo=UTC))
    finally:
        fx.PAGE = pages  # type: ignore[misc]
    assert f["open_time"].to_list() == days  # 3 + 3 + 1, no repeats at page edges
    assert [p["includeFirst"] for p in seen] == ["true", "false", "false"]
    assert seen[0]["price"] == "MBA"
    assert seen[0]["dailyAlignment"] == "17"

    refused = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"errorMessage": "no"}))
    )
    with pytest.raises(fx.FxError, match="HTTP 401") as e:
        fx.fetch_daily("EURUSD", refused, "secret", datetime(2010, 1, 1, tzinfo=UTC))
    assert "secret" not in str(e.value)


def frame(times: list[str], **kw: Any) -> pl.DataFrame:
    return fx.parse([candle(t, **kw) for t in times])


def test_a_clean_series_across_a_weekend_and_a_clock_change() -> None:
    r = fx.check(frame(WEEK + NEXT), "EURUSD", HOLDOUT)
    assert r.is_clean, r.summary()
    assert (r.gaps, r.misaligned, r.weekend_candles) == ([], 0, 0)
    assert r.median_spread_pips == pytest.approx(2.0)
    assert "verdict: CLEAN" in r.summary()


def test_problems_are_reported_not_repaired() -> None:
    times = [WEEK[0], WEEK[1], WEEK[3], WEEK[4]]  # Tuesday's candle is missing
    gap = fx.check(frame(times), "EURUSD", HOLDOUT)
    assert gap.is_clean  # a gap is listed, not fatal
    assert [(g.after.day, g.before.day, g.missing_days) for g in gap.gaps] == [(2, 4, 1)]

    dup = fx.check(frame([WEEK[0], WEEK[0], WEEK[1]]), "EURUSD", HOLDOUT)
    assert dup.duplicates == 1
    assert not dup.is_clean
    assert "FAILED (not saved)" in dup.summary()

    odd = fx.check(frame(["2015-03-02T12:00:00"]), "EURUSD", HOLDOUT)
    assert odd.misaligned == 1
    assert not odd.is_clean

    crossed = fx.check(frame([WEEK[0]], half=-0.0001), "EURUSD", HOLDOUT)
    assert crossed.crossed == 1
    assert not crossed.is_clean

    saturday = fx.check(
        frame(["2005-01-01T22:00:00", "2005-01-02T22:00:00"], ticks=1), "EURUSD", HOLDOUT
    )
    assert saturday.weekend_candles == 1
    assert saturday.is_clean
    assert saturday.single_tick_days == 2
    assert saturday.last_single_tick == datetime(2005, 1, 2, 22, tzinfo=UTC)


def test_the_spread_is_measured_before_the_holdout_only() -> None:
    before = fx.parse([candle(WEEK[0], half=0.0001)])
    after = fx.parse([candle("2023-03-01T22:00:00", half=0.001)])
    r = fx.check(pl.concat([before, after]), "EURUSD", HOLDOUT)
    assert r.median_spread_pips == pytest.approx(2.0)
    assert fx.check(after, "EURUSD", HOLDOUT).median_spread_pips is None


def test_saving_and_loading(tmp_path: Path) -> None:
    f = frame(WEEK)
    fx.save(f.reverse(), tmp_path, "eurusd")
    assert fx.path(tmp_path, "EURUSD").name == "EURUSD-1d.parquet"
    assert fx.load(tmp_path, "EURUSD").equals(f)  # sorted on save
    with pytest.raises(fx.FxError, match="kestrel ingest-fx"):
        fx.load(tmp_path, "GBPUSD")


def test_the_cli_refuses_pairs_outside_the_fixed_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(fx.TOKEN_ENV, "secret")
    base = ["ingest-fx", "--data-dir", str(tmp_path), "--config", REPO_CONFIG]
    r = CliRunner().invoke(app, [*base, "--pair", "XAUUSD"])
    assert r.exit_code == 1
    assert "not in [fx] pairs" in r.output
    monkeypatch.delenv(fx.TOKEN_ENV)
    r = CliRunner().invoke(app, [*base, "--pair", "EURUSD"])
    assert r.exit_code == 1
    assert fx.TOKEN_ENV in r.output
