"""Warm-up (kestrel.backtest.warmup): a backtest's strategy reads history from before its
start, the account still opens at the start, and a coin listed later is scored from when
the strategy's lookback has filled, with holding scored over the same days."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from kestrel.backtest import warmup
from kestrel.backtest.runs import RunRequest, RunStore, execute
from kestrel.config import load_config
from kestrel.data.binance_vision import Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategies.trend import TrendVolTarget
from kestrel.strategy import resolve_params, spec_for
from tests.backtest.helpers import bars

REPO_CONFIG = Path(__file__).resolve().parents[1] / "kestrel.toml"
DAY = timedelta(days=1)
START = datetime(2022, 1, 1, tzinfo=UTC)


def rising(first: datetime, until: datetime = datetime(2023, 1, 1, tzinfo=UTC)) -> pl.DataFrame:
    """Daily bars rising 0.3% a day with a small wiggle, so the trend rule is in from the
    moment its lookbacks fill."""
    n = (until - first).days
    closes = [100 * 1.003**i * (1 + 0.01 * (-1) ** i) for i in range(n)]
    return bars(closes, start=first, step=DAY)


def request(strategy: str, symbol: str = "BTCUSDT") -> RunRequest:
    return RunRequest(strategy, symbol, Interval.D1, Month(2022, 1), Month(2022, 12), Decimal(1000))


def test_the_strategy_reads_history_and_trades_from_the_start(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.D1, rising(datetime(2021, 1, 1, tzinfo=UTC)))
    outcome = execute(request("trend-vol-target"), store, load_config(REPO_CONFIG))
    result = outcome.result
    assert result.start == START == outcome.benchmark.start
    assert result.history_from == datetime(2021, 1, 1, tzinfo=UTC)
    # Warm on the first close, so it buys at the second open, as holding does.
    assert result.fills[0].time == START + DAY == outcome.benchmark.fills[0].time
    assert all(f.time >= START for f in result.fills)
    # The account opens with its cash: nothing carried in, nothing rebased.
    assert result.equity["equity"][0] == pytest.approx(1000)
    assert result.equity["weight"][0] == 0


def test_the_weights_are_those_of_the_whole_history() -> None:
    frame = rising(datetime(2021, 1, 1, tzinfo=UTC))
    history, scored = warmup.split(frame, START)
    full = TrendVolTarget("BTCUSDT").target_weights(frame)
    warmed = warmup.warmed(TrendVolTarget("BTCUSDT"), history)
    assert warmed.name == TrendVolTarget("BTCUSDT").name
    got = warmed.target_weights(scored)  # type: ignore[union-attr]
    assert got.to_list() == full.tail(scored.height).to_list()
    assert got[0] > 0
    assert warmup.warmed(TrendVolTarget("BTCUSDT"), history.clear()).__class__ is TrendVolTarget


def test_a_coin_listed_late_is_scored_once_the_lookback_has_filled(tmp_path: Path) -> None:
    listed = datetime(2022, 3, 1, tzinfo=UTC)
    store = ParquetStore(tmp_path)
    store.write("SOLUSDT", Interval.D1, rising(listed))
    outcome = execute(request("trend-vol-target", "SOLUSDT"), store, load_config(REPO_CONFIG))
    days = warmup.warmup_days("trend-vol-target", resolve_params("trend-vol-target", {}))
    assert days == 120
    assert outcome.result.start == listed + 120 * DAY == outcome.benchmark.start
    assert outcome.result.history_from == listed
    assert outcome.result.fills[0].time == listed + 121 * DAY  # trades on day one


def test_holding_reads_nothing_and_is_its_own_benchmark(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.D1, rising(datetime(2021, 1, 1, tzinfo=UTC)))
    outcome = execute(request("buy-and-hold"), store, load_config(REPO_CONFIG))
    assert outcome.result is outcome.benchmark
    assert outcome.result.start == START


def test_a_saved_run_records_both_dates(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.D1, rising(datetime(2021, 1, 1, tzinfo=UTC)))
    config = load_config(REPO_CONFIG)
    run_id = RunStore(tmp_path).save(execute(request("trend-vol-target"), store, config), config)
    meta = json.loads((tmp_path / "runs" / run_id / "run.json").read_text())
    assert meta["scored_from"] == "2022-01-01T00:00:00+00:00"
    assert meta["history_from"] == "2021-01-01T00:00:00+00:00"


def test_a_gap_in_the_history_is_refused(tmp_path: Path) -> None:
    frame = rising(datetime(2021, 1, 1, tzinfo=UTC))
    gap = (pl.col("open_time") >= datetime(2021, 6, 1, tzinfo=UTC)) & (
        pl.col("open_time") < datetime(2021, 6, 5, tzinfo=UTC)
    )
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, frame.filter(~gap))
    with pytest.raises(ValueError, match=r"gap|missing"):
        execute(request("trend-vol-target"), ParquetStore(tmp_path), load_config(REPO_CONFIG))


class Fixed:
    """A portfolio strategy that holds BTC on every bar after its tenth."""

    name = "fixed"

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        times = panel["open_time"].unique().sort()
        return pl.DataFrame(
            {"open_time": times[10:], "symbol": ["BTCUSDT"] * max(0, len(times) - 10)},
            schema={"open_time": pl.Datetime("us", "UTC"), "symbol": pl.Utf8},
        ).with_columns(weight=pl.lit(0.5))


def test_a_portfolio_strategy_reads_history_and_decides_from_the_start() -> None:
    panel = rising(START - 20 * DAY, START + 5 * DAY).with_columns(symbol=pl.lit("BTCUSDT"))
    history, scored = warmup.split(panel, START)
    alone = Fixed().target_weights(scored)
    assert alone.is_empty()  # 5 bars: never reaches its tenth
    warmed = warmup.warmed_portfolio(Fixed(), history)
    got = warmed.target_weights(scored)
    assert got["open_time"].to_list() == scored["open_time"].to_list()
    assert warmup.warmed_portfolio(Fixed(), history.clear()).__class__ is Fixed


def test_the_declared_warm_ups() -> None:
    def days(key: str) -> int:
        return warmup.warmup_days(key, resolve_params(key, {}))

    assert days("btc-regime-trend") == 50
    assert days("btc-rotation-8") == 365  # a year of history per coin
    assert days("stablecoin-vol-hold") == 30
    assert days("buy-and-hold") == 0
    assert warmup.warmup_days("trend-vol-target", {**resolve_params("trend-vol-target", {}),
                                                   "slow_days": 200}) == 200  # fmt: skip


def test_a_warm_up_longer_than_the_history_read_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = spec_for("trend-vol-target")
    monkeypatch.setattr(
        warmup, "spec_for", lambda key: replace(spec, warmup=lambda p: warmup.HISTORY_DAYS + 1)
    )
    with pytest.raises(ValueError, match="at most 400"):
        warmup.warmup_days("trend-vol-target", {})


def test_robustness_scores_every_setting_and_holding_over_the_same_days(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from kestrel.cli import app
    from kestrel.research import SECTIONS, ResearchStore
    from kestrel.validation.log import ValidationLog

    listed = datetime(2023, 3, 1, tzinfo=UTC)
    ParquetStore(tmp_path).write(
        "SOLUSDT", Interval.D1, rising(listed, datetime(2024, 6, 1, tzinfo=UTC))
    )
    notes = ResearchStore(tmp_path / "r")
    note = notes.create("tvt")
    notes.edit(note.slug, 1, {k: "x" for k in SECTIONS}, "trend-vol-target")
    notes.freeze(note.slug, 1)
    args = ["robustness", "SOLUSDT", "--strategy", "trend-vol-target", "--research", note.slug,
            "--interval", "1d", "--start", "2023-01", "--end", "2024-04",
            "--data-dir", str(tmp_path), "--config", str(REPO_CONFIG),
            "--research-dir", str(tmp_path / "r")]  # fmt: skip
    got = CliRunner().invoke(app, args)
    assert got.exit_code == 0, got.output
    (study,) = ValidationLog(tmp_path).studies()
    # The slowest neighbour (slow_days 180) sets the day every setting is scored from.
    assert study["detail"]["scored_from"] == (listed + 180 * DAY).isoformat()
    assert study["detail"]["held_sharpe"] > 0


def test_the_scorecard_compares_neighbours_with_holding_over_the_grids_days() -> None:
    from kestrel.validation.scorecard import grade
    from tests.test_scorecard import robustness, run, statuses

    study = robustness([1.2] * 10)
    assert statuses(grade(run(), 0.97, [study]))["Robustness"] == "pass"
    study["detail"]["held_sharpe"] = 1.3  # holding over the grid's own (later) days
    assert statuses(grade(run(), 0.97, [study]))["Robustness"] == "fail"
