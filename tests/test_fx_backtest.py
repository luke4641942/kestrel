"""Forex runs (P6 step 3d): calendar-day metrics, the forex trial pool, the forex strategy
checks, and a frozen note run end to end with ``fx_backtest``. Offline, synthetic data."""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from kestrel.backtest.fx_engine import FxResult
from kestrel.backtest.fx_runs import FxRunStore, calendar_equity, compute_fx_metrics
from kestrel.config import parse_config
from kestrel.data import external, fx, rates
from kestrel.fx_backtest import fx_backtest
from kestrel.fx_strategy import FX_STRATEGIES, FxSpec, register, validated_fx_weights
from kestrel.research import SECTIONS, ResearchError, ResearchStore, run_target
from kestrel.validation.deflated import (
    TrialSet,
    every_pool,
    format_pools,
    pool_of,
    trial_set,
)
from tests.backtest.test_fx_engine import pair_frame

DAY = timedelta(days=1)
T0 = datetime(2010, 1, 3, 22, tzinfo=UTC)  # Sunday 17:00 New York


def week_days(n_weeks: int) -> list[datetime]:
    """Opens Sunday to Thursday, 22:00 UTC (winter), for ``n_weeks`` weeks."""
    return [T0 + timedelta(weeks=w, days=d) for w in range(n_weeks) for d in range(5)]


def test_calendar_equity_carries_over_the_weekend() -> None:
    closes = [T0 + DAY * k for k in (1, 2, 3, 4, 5, 8)]  # Mon..Fri closes, then Monday
    eq = pl.DataFrame(
        {
            "close_time": closes,
            "equity": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "gross": [1.0] * 6,
            "net": [1.0] * 6,
        }
    )
    result = FxResult("t", ("EURAUD",), 100.0, "AUD", equity=eq)
    daily = calendar_equity(result)
    assert daily["date"][0] == date(2010, 1, 3)  # the start, the day before the first close
    assert daily["equity"].to_list() == [100, 100, 101, 102, 103, 104, 104, 104, 105]
    m = compute_fx_metrics(result)
    assert m.days == 8
    assert m.total_return == pytest.approx(0.05)


def test_forex_runs_have_their_own_pool() -> None:
    crypto = {
        "strategy": "x",
        "created_at": "2026-01-01T00:00:00+00:00",
        "metrics": {"sharpe": 1.0},
    }
    forex = {**crypto, "market": "forex"}
    assert (pool_of(crypto), pool_of(forex)) == ("main", "forex")
    runs: list[dict[str, Any]] = []
    for i, (market, sharpe) in enumerate([("forex", 0.5), ("forex", -0.2), (None, 1.0)]):
        runs.append(
            {
                "strategy": f"s{i}", "params": {}, "symbol": "X", "interval": "1d",
                "start": "a", "end": "b", "initial_cash": 1, "research": None,
                "created_at": f"2026-01-0{i + 1}T00:00:00+00:00", "metrics": {"sharpe": sharpe},
                **({"market": market} if market else {}),
            }
        )  # fmt: skip
    assert trial_set(runs).count == 1  # the crypto bar never counts forex runs
    pools = every_pool(runs)
    assert (pools["main"].count, pools["forex"].count, pools["confirmation"].count) == (1, 2, 0)
    text = format_pools(pools, "forex")
    assert "forex pool" in text
    assert "from 2 trials; 3 trials across all pools (crypto 1, confirmation 0, forex 2)" in text


class Peeking:
    name = "peeks"

    def target_weights(self, bars: dict[str, pl.DataFrame], _: rates.Rates) -> pl.DataFrame:
        f = bars["EURAUD"]
        up = (f["close"].shift(-1) > f["close"]).fill_null(False)  # tomorrow's close
        return pl.DataFrame({"open_time": f["open_time"], "EURAUD": up.cast(pl.Float64)})


class Following:
    name = "follows"

    def __init__(self, weight: float = 1.0) -> None:
        self.weight = weight

    def target_weights(self, bars: dict[str, pl.DataFrame], _: rates.Rates) -> pl.DataFrame:
        f = bars["EURAUD"]
        up = (f["close"] > f["close"].shift(1)).fill_null(False)  # today's close only
        return pl.DataFrame(
            {"open_time": f["open_time"], "EURAUD": up.cast(pl.Float64) * self.weight}
        )


def wavy(n: int = 60) -> dict[str, pl.DataFrame]:
    prices = [100 + 5 * math.sin(i / 3) for i in range(n)]
    return {"EURAUD": pair_frame(prices, times=week_days(n // 5 + 1)[:n])}


def test_the_forex_strategy_checks() -> None:
    bars, r = wavy(), rates.Rates({})
    assert validated_fx_weights(Following(), bars, r, 3).height == 60
    with pytest.raises(ValueError, match="lookahead: EURAUD's weight"):
        validated_fx_weights(Peeking(), bars, r, 3)
    with pytest.raises(ValueError, match=r"4\.00x gross, over the 3x cap"):
        validated_fx_weights(Following(4.0), bars, r, 3)
    with pytest.raises(ValueError, match="start with 'fx:'"):
        register(FxSpec("trend", "no prefix", Following))


@pytest.fixture
def registered() -> Iterator[str]:
    key = "fx:test-follows"
    register(FxSpec(key, "test only", Following))
    yield key
    del FX_STRATEGIES[key]


def fx_config(**fx_overrides: Any) -> Any:
    from tests.backtest.helpers import config

    doc = dict(config().document)
    doc["fx"] = {
        "holdout_start": date(2023, 1, 1),
        "pairs": ["EURAUD"],
        "account_currency": "AUD",
        "max_gross_leverage": 3,
        "financing_markup_pct": 1.0,
        **fx_overrides,
    }
    return parse_config(doc)


def write_data(data_dir: Path) -> None:
    n = 26 * 5  # half a year of trading days from 2010-01
    prices = [100 * (1 + 0.002 * i) + 3 * math.sin(i / 4) for i in range(n)]
    fx.save(pair_frame(prices, times=week_days(27)[:n]), data_dir, "EURAUD")
    months = [date(2009, m, 1) for m in range(1, 13)] + [date(2010, m, 1) for m in range(1, 13)]
    for currency in rates.COUNTRY:
        value = {"EUR": 1.0, "AUD": 4.0}.get(currency, 2.0)
        frame = pl.DataFrame(
            {"date": months, "value": [value] * len(months)}, schema=external.SCHEMA
        )
        external.save(frame, data_dir, rates.series(currency))


def frozen_note(research_dir: Path, key: str) -> str:
    store = ResearchStore(research_dir)
    slug = store.create("A test forex note").slug
    store.edit(slug, 1, dict.fromkeys(SECTIONS, "x"), key)
    store.freeze(slug, 1)
    return slug


def test_a_frozen_forex_note_runs_saves_and_counts(tmp_path: Path, registered: str) -> None:
    data, research = tmp_path / "data", tmp_path / "research"
    write_data(data)
    slug = frozen_note(research, registered)
    store = ResearchStore(research)

    run = fx_backtest(store, slug, data, fx_config(), "2010-01", "2010-06", 500)
    (saved,) = FxRunStore(data).summaries()
    assert saved["id"] == run.run_id
    assert saved["market"] == "forex"
    assert saved["research"] == {"slug": slug, "version": 1}
    assert saved["detail"]["financing_markup_pct"] == 1.0
    assert store.get(slug).status == "backtested"
    assert "forex pool" in run.report
    assert "1 trials across all pools (crypto 0, confirmation 0, forex 1)" in run.report
    assert math.isfinite(saved["metrics"]["sharpe"])
    # Long EUR against AUD pays the rate gap (1% - 4%) and the markup: financing is negative.
    assert saved["metrics"]["financing"] < 0

    with pytest.raises(ResearchError, match="trades forex: run it with kestrel fx-backtest"):
        run_target(store, registered, slug)
    with pytest.raises(ValueError, match="forex holdout"):
        fx_backtest(
            store, slug, data, fx_config(holdout_start=date(2010, 3, 1)), "2010-01", "2010-06", 500
        )


def test_a_crypto_note_is_not_a_forex_run(tmp_path: Path, registered: str) -> None:
    store = ResearchStore(tmp_path)
    slug = store.create("crypto").slug
    store.edit(slug, 1, dict.fromkeys(SECTIONS, "x"), "trend-vol-target")
    store.freeze(slug, 1)
    with pytest.raises(ValueError, match="not a forex strategy"):
        fx_backtest(store, slug, tmp_path, fx_config(), "2010-01", "2010-06", 500)
    assert TrialSet(0, 0).expected_max_sharpe == 0
