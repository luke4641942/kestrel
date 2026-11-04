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
from kestrel.strategy import STRATEGIES
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
    monkeypatch.setitem(STRATEGIES, "half", (lambda s: FixedWeights([0.5] * 60, s), "Half in."))
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
    listed = runs.list()
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
    assert [r["id"] for r in runs.list()] == [run_id]
