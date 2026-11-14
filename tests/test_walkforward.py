"""Walk-forward testing on a year of synthetic daily bars."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.backtest.engine import HoldoutError, run_backtest
from kestrel.backtest.metrics import compute_metrics
from kestrel.cli import app
from kestrel.data.binance_vision import Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategy import make_strategy
from kestrel.validation.log import ValidationLog
from kestrel.validation.walkforward import Fold, WalkForward, format_walk_forward, walk_forward
from tests.backtest.helpers import bars, config

D1 = Interval.D1
REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")
GRID = {"fast": [3, 5], "slow": [10, 20]}


def daily(
    start: datetime = datetime(2023, 1, 1, tzinfo=UTC), n: int = 365, seed: int = 4
) -> pl.DataFrame:
    rnd = random.Random(seed)
    closes, price = [], 100.0
    for i in range(n):
        drift = 0.004 if (i // 60) % 2 == 0 else -0.003  # alternating trends to choose between
        price *= math.exp(rnd.gauss(drift, 0.02))
        closes.append(price)
    return bars([closes[0], *closes[:-1]], closes=closes, start=start, step=timedelta(days=1))


def run(frame: pl.DataFrame | None = None, **over: object) -> WalkForward:
    kwargs: dict[str, object] = dict(
        start=Month.parse("2023-01"), end=Month.parse("2023-12"), train_months=3, test_months=2
    )
    kwargs.update(over)
    return walk_forward(
        "ma-trend",
        "BTCUSDT",
        daily() if frame is None else frame,
        D1,
        config(costs=False),
        Decimal(1000),
        GRID,
        **kwargs,  # type: ignore[arg-type]
    )


def test_folds_roll_forward_and_test_windows_follow_each_other() -> None:
    wf = run()
    # Train Jan-Mar, test Apr-May; then Mar-May / Jun-Jul; May-Jul / Aug-Sep; Jul-Sep /
    # Oct-Nov. A fifth would test Dec-Jan, past the end.
    assert [f.test_start.month for f in wf.folds] == [4, 6, 8, 10]
    assert all(a.test_end == b.test_start for a, b in zip(wf.folds, wf.folds[1:], strict=False))
    assert wf.configurations == 4
    # Every day of the test windows (April to November) has one out-of-sample return.
    assert (
        len(wf.oos_returns)
        == (datetime(2023, 12, 1, tzinfo=UTC) - datetime(2023, 4, 1, tzinfo=UTC)).days
    )


def test_each_fold_chooses_the_best_grid_point_on_its_training_window() -> None:
    wf = run()
    first = wf.folds[0]
    train = daily().filter(pl.col("open_time") < first.test_start)
    sharpes = {
        (f, s): compute_metrics(
            run_backtest(
                make_strategy("ma-trend", "BTCUSDT", {"fast": f, "slow": s}),
                train,
                D1,
                config(costs=False),
                Decimal(1000),
            )
        ).sharpe
        for f in GRID["fast"]
        for s in GRID["slow"]
    }
    best = max(sharpes, key=lambda k: sharpes[k])
    assert (first.chosen["fast"], first.chosen["slow"]) == best
    assert first.train_sharpe == pytest.approx(sharpes[best])


def test_the_verdict_follows_the_backlogs_rule() -> None:
    def wf(train_sharpe: float, test: list[float]) -> WalkForward:
        t = datetime(2023, 1, 1, tzinfo=UTC)
        fold = Fold(t, t, t, {"fast": 3}, train_sharpe, test, [0.0, 0.01])
        return WalkForward(
            "ma-trend", "BTCUSDT", D1, {"fast": [3]}, [fold], [({"fast": 3}, train_sharpe)]
        )

    steady = [0.01, 0.012, 0.009, 0.011] * 20  # a high out-of-sample Sharpe
    assert wf(2.0, steady).survives
    assert "holds up" in format_walk_forward(wf(2.0, steady))
    assert not wf(2.0, [0.01, -0.012] * 40).survives  # out of sample it falls apart
    assert "KILL" in format_walk_forward(wf(2.0, [0.01, -0.012] * 40))
    assert wf(-1.0, steady).ratio is None  # no meaningful ratio to a negative in-sample
    assert wf(1.0, [0.01]).oos_sharpe is None
    assert wf(1.0, [0.0, 0.0]).oos_sharpe is None


def test_each_test_window_is_compared_with_holding_on_sharpe() -> None:
    t = datetime(2023, 1, 1, tzinfo=UTC)
    steady = [0.01, 0.012, 0.009, 0.011] * 20  # high Sharpe
    noisy = [0.02, -0.018] * 40  # low Sharpe, positive drift

    def fold(test: list[float], held: list[float]) -> Fold:
        return Fold(t, t, t, {"fast": 3}, 1.0, test, held)

    assert fold(steady, noisy).beats_holding
    assert not fold(noisy, steady).beats_holding
    assert not fold([0.0] * 10, noisy).beats_holding  # in cash all window: no Sharpe
    assert fold(noisy, [0.0] * 10).beats_holding  # a flat market is beaten by any Sharpe
    folds = [fold(steady, noisy), fold(noisy, steady), fold(steady, noisy)]
    wf = WalkForward("ma-trend", "BTCUSDT", D1, {"fast": [3]}, folds, [({"fast": 3}, 1.0)])
    assert wf.windows_beating_holding == 2
    assert wf.benchmark_sharpe == pytest.approx(
        WalkForward("x", "B", D1, {}, [fold(noisy + steady + noisy, [])], []).oos_sharpe
    )
    assert "Beats holding's Sharpe in         2 of 3 test windows" in format_walk_forward(wf)


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"train_months": 0}, "at least a month"),
        ({"train_months": 11, "test_months": 2}, "shorter than one"),
    ],
)
def test_bad_windows(over: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        run(**over)


def test_a_grid_is_required_and_impossible_points_are_skipped() -> None:
    with pytest.raises(ValueError, match="grid"):
        walk_forward(
            "ma-trend",
            "BTCUSDT",
            daily(),
            D1,
            config(),
            Decimal(1000),
            {},
            start=Month.parse("2023-01"),
            end=Month.parse("2023-12"),
            train_months=3,
            test_months=2,
        )
    with pytest.raises(ValueError, match="no grid point"):
        walk_forward(
            "ma-trend",
            "BTCUSDT",
            daily(),
            D1,
            config(),
            Decimal(1000),
            {"fast": [30], "slow": [20]},
            start=Month.parse("2023-01"),
            end=Month.parse("2023-12"),
            train_months=3,
            test_months=2,
        )


def test_the_holdout_is_never_used() -> None:
    late = daily(start=datetime(2025, 3, 1, tzinfo=UTC), n=300)
    with pytest.raises(HoldoutError):
        run(late, start=Month.parse("2025-03"), end=Month.parse("2025-12"))


runner = CliRunner()


def test_the_cli(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", D1, daily())
    args = [
        "walk-forward",
        "BTCUSDT",
        "--strategy",
        "ma-trend",
        "--grid",
        "fast=3,5",
        "--grid",
        "slow=10,20",
        "--interval",
        "1d",
        "--start",
        "2023-01",
        "--end",
        "2023-12",
        "--train-months",
        "3",
        "--test-months",
        "2",
    ]
    result = runner.invoke(
        app,
        [
            *args,
            "--data-dir",
            str(tmp_path),
            "--config",
            REPO_CONFIG,
            "--research-dir",
            str(tmp_path / "r"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "4 folds, 4 grid points each" in result.output
    assert "Every grid point is a trial" in result.output
    (study,) = ValidationLog(tmp_path).studies()
    assert study["detail"]["windows_beating_holding"] == run().windows_beating_holding
    assert study["detail"]["benchmark_sharpe"] == pytest.approx(run().benchmark_sharpe)


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--strategy", "ma-trend", "--grid", "fast"], "--grid must be"),
        (["--strategy", "ma-trend", "--grid", "fast=a,b"], "--grid must be"),
        (["--strategy", "trend-vol-target", "--grid", "fast_days=10,20"], "research note"),
    ],
)
def test_the_cli_refuses_bad_requests(tmp_path: Path, extra: list[str], message: str) -> None:
    args = [
        "walk-forward",
        "BTCUSDT",
        "--interval",
        "1d",
        "--start",
        "2023-01",
        "--end",
        "2023-12",
        *extra,
    ]
    result = runner.invoke(
        app,
        [
            *args,
            "--data-dir",
            str(tmp_path),
            "--config",
            REPO_CONFIG,
            "--research-dir",
            str(tmp_path / "r"),
        ],
    )
    assert result.exit_code == 1
    assert message in result.output


def test_a_grid_point_that_never_trades_is_skipped_not_chosen() -> None:
    # A 300-day slow average never fills in a 3-month window: no trades, flat equity, no
    # Sharpe. It is neither chosen nor counted as a look.
    wf = walk_forward(
        "ma-trend", "BTCUSDT", daily(), D1, config(costs=False), Decimal(1000),
        {"fast": [3], "slow": [10, 300]},
        start=Month.parse("2023-01"), end=Month.parse("2023-12"), train_months=3, test_months=2,
    )  # fmt: skip
    assert all(f.chosen["slow"] == 10 for f in wf.folds)
    assert [p for p, _ in wf.looks] == [{"fast": 3, "slow": 10}]
