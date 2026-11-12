"""Parameter robustness, the validation log, and how both feed the deflated Sharpe."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from kestrel.backtest.metrics import Metrics
from kestrel.cli import app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategy import Param
from kestrel.validation.deflated import trial_set
from kestrel.validation.log import Look, Study, ValidationLog
from kestrel.validation.robustness import (
    Robustness,
    Setting,
    check_robustness,
    format_robustness,
    neighbourhood,
)
from tests.test_walkforward import daily

REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")
PARAMS = (Param("lookback", 20, 2, 500, ""), Param("target_r", 3, 1, 20, ""))
CENTER = {"lookback": 20, "target_r": 3}


def metrics(sharpe: float, **over: Any) -> Metrics:
    fields: dict[str, Any] = dict(
        start=datetime(2020, 1, 1, tzinfo=UTC), end=datetime(2021, 1, 1, tzinfo=UTC), days=366,
        final_equity=1100.0, total_return=0.1, cagr=0.1, volatility=0.5, sharpe=sharpe,
        sharpe_ci=(0.0, 1.0), psr=0.9, sortino=1.0, max_drawdown=-0.2,
        longest_underwater=timedelta(0),
        skew=0.0, kurtosis=3.0, exposure=0.5, trades=40, rejections=0, band_skips=0, fees=1.0,
        spread_costs=0.1, impact_costs=0.0, turnover=2.0,
    )  # fmt: skip
    return Metrics(**{**fields, **over})


def test_each_parameter_moves_alone_within_its_range() -> None:
    settings = neighbourhood(PARAMS, CENTER)
    # One parameter at a time; the other stays at its starting value.
    assert all(s["target_r"] == 3 for name, s in settings if name == "lookback")
    assert all(s["lookback"] == 20 for name, s in settings if name == "target_r")
    assert [s["lookback"] for n, s in settings if n == "lookback"] == [10, 15, 25, 30]
    # 3 x 0.5 = 1.5 and 3 x 0.75 = 2.25 both round to 2 (kept once); 3.75 to 4; 4.5 to 5.
    assert [s["target_r"] for n, s in settings if n == "target_r"] == [2, 4, 5]
    tight = neighbourhood((Param("swing_bars", 2, 2, 3, ""),), {"swing_bars": 2})
    assert [s["swing_bars"] for _, s in tight] == [3]  # 1 is below the range, 2 is the start


def run(sharpe_of: dict[int, float], center: float = 1.0) -> Robustness:
    """Sharpe as a function of the lookback; target_r settings all return ``center``."""

    def evaluate(p: dict[str, int]) -> Metrics:
        if p["lookback"] == 20:
            return metrics(center)
        return metrics(sharpe_of[p["lookback"]])

    return check_robustness("channel-breakout", PARAMS, CENTER, evaluate)


def test_a_plateau_is_robust() -> None:
    r = run({10: 0.6, 15: 0.9, 25: 0.8, 30: 0.5})
    assert r.verdict == "ROBUST"
    assert r.positive_share == 1
    assert not r.is_peak  # the target_r neighbours equal the start
    assert "ROBUST" in format_robustness(r, "BTCUSDT 4h")


def test_a_lone_peak_is_fragile() -> None:
    r = check_robustness(
        "channel-breakout", PARAMS, CENTER, lambda p: metrics(1.0 if p == CENTER else -0.2)
    )
    assert r.verdict == "FRAGILE"
    assert r.is_peak
    report = format_robustness(r, "BTCUSDT 4h")
    assert "FRAGILE" in report
    assert "beats every neighbour" in report


def test_neighbours_mostly_positive_but_much_weaker_is_fragile() -> None:
    # All positive, but the median keeps under half the start: the result leans on its values.
    r = check_robustness(
        "channel-breakout", PARAMS, CENTER, lambda p: metrics(2.0 if p == CENTER else 0.3)
    )
    assert r.positive_share == 1
    assert r.verdict == "FRAGILE"


def test_no_edge_to_be_robust_about() -> None:
    r = run({10: 0.5, 15: 0.5, 25: 0.5, 30: 0.5}, center=-0.1)
    assert r.verdict == "NO EDGE"
    assert "nothing to be robust about" in format_robustness(r, "BTCUSDT 4h")


def test_settings_that_cannot_run_are_reported_not_counted() -> None:
    def evaluate(p: dict[str, int]) -> Metrics:
        if p["lookback"] == 10:
            raise ValueError("need 1 <= fast < slow")
        return metrics(math.nan) if p["lookback"] == 30 else metrics(1.0)

    r = check_robustness("channel-breakout", PARAMS, CENTER, evaluate)
    assert r.neighbours[0].error == "need 1 <= fast < slow"
    assert r.neighbours[3].sharpe is None  # flat equity: no Sharpe
    assert len(r.scored) == 5
    assert "could not run" in format_robustness(r, "BTCUSDT 4h")


def test_nothing_scored_means_no_verdict_in_its_favour() -> None:
    r = Robustness("x", Setting(None, CENTER, 1.0, 0.1, -0.1, None, 10), [])
    assert (r.positive_share, r.median_sharpe, r.verdict, r.is_peak) == (
        None,
        None,
        "FRAGILE",
        False,
    )
    assert "n/a" in format_robustness(r, "BTCUSDT 4h")


# ---------- the validation log and the trial count ----------


def study(created: str, sharpes: list[float | None], kind: Any = "robustness") -> dict[str, Any]:
    return {
        "created_at": created, "kind": kind, "strategy": "ma-trend", "symbol": "BTCUSDT",
        "interval": "4h", "start": "2020-01", "end": "2025-10",
        "looks": [{"params": {"fast": i}, "sharpe": s} for i, s in enumerate(sharpes)],
    }  # fmt: skip


def test_studies_are_saved_and_listed_newest_first(tmp_path: Path) -> None:
    log = ValidationLog(tmp_path)
    assert log.studies() == []
    first = log.save(
        Study(
            "robustness",
            "ma-trend",
            "BTCUSDT",
            "4h",
            "2020-01",
            "2025-10",
            [Look({"fast": 3}, 0.5)],
            "FRAGILE",
        )
    )
    second = log.save(
        Study("walk-forward", "ma-trend", "BTCUSDT", "4h", "2020-01", "2025-10", [], "KILL")
    )
    assert [s["id"] for s in log.studies()] == [second, first]
    assert not list((tmp_path / "validation").glob(".*.tmp"))  # written atomically


def test_every_study_setting_counts_as_a_trial_once() -> None:
    runs = [
        {
            "strategy": "ma-trend",
            "created_at": "2026-01-01",
            "params": {},
            "research": None,
            "symbol": "BTCUSDT",
            "interval": "4h",
            "start": "2020-01",
            "end": "2025-10",
            "initial_cash": "330",
            "used_holdout": False,
            "metrics": {"sharpe": 0.5},
        }
    ]
    studies = [study("2026-01-02", [0.4, 0.8, None]), study("2026-01-03", [0.4, 0.9])]
    t = trial_set(runs, studies)
    # The run, plus fast=0 and fast=1 (the later study's values win), and fast=2 has no Sharpe.
    assert t.count == 3
    assert trial_set(runs).count == 1


# ---------- the command line ----------

runner = CliRunner()


def test_the_cli_checks_saves_and_counts(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, daily())
    args = [
        "robustness",
        "BTCUSDT",
        "--strategy",
        "ma-trend",
        "--param",
        "fast=5",
        "--param",
        "slow=20",
        "--interval",
        "1d",
        "--start",
        "2023-01",
        "--end",
        "2023-12",
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
    assert "Parameter robustness" in result.output
    assert "Starting setting: fast=5 slow=20" in result.output
    (saved,) = ValidationLog(tmp_path).studies()
    assert saved["kind"] == "robustness"
    assert len(saved["looks"]) >= 5
    assert saved["detail"]["center"] == {"fast": 5, "slow": 20}


def test_the_cli_runs_a_universe_strategy(tmp_path: Path) -> None:
    from tests.backtest.test_universe_runs import _store_universe

    _store_universe(tmp_path)
    args = [
        "robustness",
        "UNIVERSE",
        "--strategy",
        "xs-momentum",
        "--research",
        "mom",
        "--param",
        "min_history_days=30",
        "--interval",
        "1d",
        "--start",
        "2023-01",
        "--end",
        "2023-12",
    ]
    research = tmp_path / "r"
    from kestrel.research import SECTIONS, ResearchStore

    notes = ResearchStore(research)
    note = notes.create("mom")
    notes.edit(note.slug, 1, {k: "x" for k in SECTIONS}, "xs-momentum")
    notes.freeze(note.slug, 1)
    args[args.index("mom")] = note.slug
    result = runner.invoke(
        app,
        [
            *args,
            "--data-dir",
            str(tmp_path),
            "--config",
            REPO_CONFIG,
            "--research-dir",
            str(research),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "UNIVERSE 1d" in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--start", "2023-01", "--end", "2023-12"], "no stored BTCUSDT 1d bars"),
        (["--start", "2023-01", "--end", "2023-12", "--param", "fast=0"], "must be 2 to 200"),
    ],
)
def test_the_cli_refuses_bad_requests(tmp_path: Path, args: list[str], message: str) -> None:
    base = ["robustness", "BTCUSDT", "--strategy", "ma-trend", "--interval", "1d", *args]
    result = runner.invoke(
        app,
        [
            *base,
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


def test_walk_forward_saves_every_grid_point(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, daily())
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
    (saved,) = ValidationLog(tmp_path).studies()
    assert saved["kind"] == "walk-forward"
    assert len(saved["looks"]) == 4
    assert json.dumps(saved["detail"])  # what the report showed is kept


def test_the_api_counts_and_lists_studies(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kestrel.api.app import create_app

    ValidationLog(tmp_path).save(
        Study(
            "robustness",
            "ma-trend",
            "BTCUSDT",
            "4h",
            "2020-01",
            "2025-10",
            [Look({"fast": 3}, 0.5), Look({"fast": 5}, 1.1)],
            "FRAGILE",
        )
    )
    client = TestClient(
        create_app(tmp_path, static_dir=None, config_path=REPO_CONFIG, research_dir=tmp_path / "r")
    )
    v = client.get("/api/validation").json()
    assert v["trials"] == 2
    assert v["studies"][0]["verdict"] == "FRAGILE"
