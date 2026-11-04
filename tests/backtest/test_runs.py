from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from kestrel.backtest.engine import HoldoutError
from kestrel.backtest.metrics import compute_metrics
from kestrel.backtest.runs import RunRequest, RunStore, execute, metrics_dict
from kestrel.data.binance_vision import Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategy import STRATEGIES, StrategySpec
from tests.backtest.helpers import FixedWeights, bars, config

H4 = Interval.H4


def request(**kw: object) -> RunRequest:
    base: dict[str, object] = {
        "strategy": "buy-and-hold",
        "symbol": "btcusdt",
        "interval": H4,
        "start": Month(2024, 1),
        "end": Month(2024, 1),
        "initial_cash": Decimal(1000),
    }
    return RunRequest(**{**base, **kw})  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> ParquetStore:
    s = ParquetStore(tmp_path)
    s.write("BTCUSDT", H4, bars([100.0 + (i % 9) * 2 for i in range(60)]))
    return s


def test_execute_runs_strategy_and_benchmark(store: ParquetStore) -> None:
    outcome = execute(request(), store, config())
    assert outcome.result.symbol == "BTCUSDT"
    assert outcome.benchmark is outcome.result  # buy-and-hold is its own benchmark
    assert outcome.metrics.trades == 1


def test_other_strategies_get_a_separate_benchmark_run(
    store: ParquetStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = StrategySpec("half", "engine-test", "Half in.", lambda s: FixedWeights([0.5] * 60, s))
    monkeypatch.setitem(STRATEGIES, "half", spec)
    outcome = execute(request(strategy="half"), store, config())
    assert outcome.benchmark is not outcome.result
    assert outcome.benchmark.strategy == "buy-and-hold BTCUSDT"
    assert outcome.metrics.exposure < outcome.benchmark_metrics.exposure


def test_execute_without_data_explains(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ingest them first"):
        execute(request(), ParquetStore(tmp_path), config())


def test_execute_respects_the_holdout(tmp_path: Path) -> None:
    s = ParquetStore(tmp_path)
    s.write("BTCUSDT", H4, bars([100.0] * 30, start=datetime(2025, 11, 1, tzinfo=UTC)))
    with pytest.raises(HoldoutError):
        execute(request(start=Month(2025, 11), end=Month(2025, 11)), s, config())


def test_unknown_strategy(store: ParquetStore) -> None:
    with pytest.raises(ValueError, match="unknown strategy 'nope'"):
        execute(request(strategy="nope"), store, config())


def test_save_and_load_round_trip(store: ParquetStore, tmp_path: Path) -> None:
    cfg = config()
    outcome = execute(request(), store, cfg)
    runs = RunStore(tmp_path)
    run_id = runs.save(outcome, cfg)

    saved = runs.load(run_id)
    assert saved.meta["id"] == run_id
    assert saved.meta["config_fingerprint"] == cfg.fingerprint()
    assert saved.meta["config"]["costs"]["taker_fee_bps"] == 10
    assert saved.meta["metrics"]["total_return"] == pytest.approx(outcome.metrics.total_return)
    assert saved.equity.equals(outcome.result.equity)
    assert saved.fills.height == 1
    assert saved.fills["fee"][0] == pytest.approx(float(outcome.result.fills[0].fill.fee))
    assert saved.rejections.height == 0
    assert not list(runs.root.glob(".*.tmp"))


def test_list_is_newest_first_and_omits_the_config(store: ParquetStore, tmp_path: Path) -> None:
    runs = RunStore(tmp_path)
    first = runs.save(execute(request(), store, config()), config())
    second = runs.save(execute(request(initial_cash=Decimal(500)), store, config()), config())
    listed = runs.summaries()
    assert [r["id"] for r in listed] == [second, first]  # created_at has microseconds
    assert all("config" not in r for r in listed)


def test_rejections_are_saved(tmp_path: Path) -> None:
    s = ParquetStore(tmp_path)
    s.write("BTCUSDT", H4, bars([100.0] * 30))
    outcome = execute(request(initial_cash=Decimal(3)), s, config())
    saved = RunStore(tmp_path).load(RunStore(tmp_path).save(outcome, config()))
    assert "below the minimum 5" in saved.rejections["reason"][0]


def test_a_failed_save_leaves_nothing_behind(
    store: ParquetStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = execute(request(), store, config())

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", boom)
    runs = RunStore(tmp_path)
    with pytest.raises(OSError, match="disk full"):
        runs.save(outcome, config())
    assert list(runs.root.iterdir()) == []


@pytest.mark.parametrize("bad", ["../../etc", "nope", "20260101-000000-zzzzzz"])
def test_load_refuses_unknown_or_unsafe_ids(tmp_path: Path, bad: str) -> None:
    with pytest.raises(KeyError):
        RunStore(tmp_path).load(bad)


def test_metrics_dict_is_json_safe(tmp_path: Path) -> None:
    flat = ParquetStore(tmp_path)
    flat.write("BTCUSDT", H4, bars([100.0] * 30))
    m = compute_metrics(execute(request(), flat, config(costs=False)).result)
    d = metrics_dict(m)
    assert d["sharpe"] is None  # flat prices: undefined, not NaN in JSON
    assert d["longest_underwater_days"] == pytest.approx(m.longest_underwater / timedelta(days=1))
    assert isinstance(d["start"], str)
    assert d["few_trades"] is True
    assert not any(isinstance(v, float) and math.isnan(v) for v in d.values())


def test_list_ignores_folders_that_are_not_runs(store: ParquetStore, tmp_path: Path) -> None:
    runs = RunStore(tmp_path)
    run_id = runs.save(execute(request(), store, config()), config())
    # A leftover temp folder or anything hand-made must not show up as a run.
    for stray in (".20260101-000000-abcdef.tmp", "notes"):
        (runs.root / stray).mkdir()
        (runs.root / stray / "run.json").write_text("{}")
    assert [r["id"] for r in runs.summaries()] == [run_id]


def test_identical_reruns_are_one_trial_any_change_is_another() -> None:
    from kestrel.backtest.runs import count_trials

    base = {
        "research": {"slug": "x", "version": 1},
        "strategy": "ma-trend",
        "params": {"fast": 20, "slow": 100},
        "symbol": "BTCUSDT",
        "interval": "4h",
        "start": "2022-01",
        "end": "2025-10",
        "initial_cash": "330",
    }
    assert count_trials([base, dict(base)]) == 1
    assert count_trials([base, {**base, "params": {"fast": 21, "slow": 100}}]) == 2
    assert count_trials([base, {**base, "research": {"slug": "x", "version": 2}}]) == 2
    assert count_trials([base, {**base, "start": "2023-01"}]) == 2


def test_runs_record_params_and_research_link(store: ParquetStore, tmp_path: Path) -> None:
    outcome = execute(
        request(strategy="ma-trend", params={"fast": 3}, research=("trend", 1)), store, config()
    )
    runs = RunStore(tmp_path)
    runs.save(outcome, config())
    (meta,) = runs.trials("trend")
    assert meta["params"] == {"fast": 3, "slow": 100}
    assert meta["research"] == {"slug": "trend", "version": 1}
    assert meta["strategy_kind"] == "engine-test"
    assert runs.trials("other") == []


def test_bracket_runs_save_their_trades(tmp_path: Path) -> None:
    import math

    closes = [100 + 10 * math.sin(i / 7) + i * 0.05 for i in range(400)]
    s = ParquetStore(tmp_path)
    s.write(
        "BTCUSDT",
        H4,
        bars(
            closes,
            closes=closes,
            highs=[c * 1.004 for c in closes],
            lows=[c * 0.996 for c in closes],
        ),
    )
    outcome = execute(request(strategy="channel-breakout", end=Month(2024, 3)), s, config())
    runs = RunStore(tmp_path)
    saved = runs.load(runs.save(outcome, config()))
    assert saved.meta["style"] == "brackets"
    assert saved.trades.height == len(outcome.result.trades) >= 1
    assert saved.trades["r_multiple"][0] == pytest.approx(
        float(outcome.result.trades[0].r_multiple)
    )
    assert saved.meta["metrics"]["round_trips"] == saved.trades.height
    assert outcome.benchmark.style == "weights"  # the benchmark is still buy-and-hold


def test_runs_saved_before_stops_still_load(store: ParquetStore, tmp_path: Path) -> None:
    runs = RunStore(tmp_path)
    run_id = runs.save(execute(request(), store, config()), config())
    (runs.root / run_id / "trades.parquet").unlink()
    assert runs.load(run_id).trades.is_empty()
