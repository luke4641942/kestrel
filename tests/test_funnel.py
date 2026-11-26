"""The funnel (kestrel.validation.funnel): every trial through the first tests and the luck
test, counted from the same trials the luck bar counts, then what was kept."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.validation.deflated import trial_set
from kestrel.validation.funnel import funnel, kept, pool_funnel


def run(
    sharpe: float, held: float | None, at: str, strategy: str = "s", **extra: Any
) -> dict[str, Any]:
    return {
        "created_at": at,
        "strategy": strategy,
        "params": {"at": at},
        "research": None,
        "symbol": "BTCUSDT",
        "interval": "1d",
        "start": "2020-01",
        "end": "2025-10",
        "metrics": {"sharpe": sharpe, "days": 2000, "skew": 0.0, "kurtosis": 3.0},
        "initial_cash": "330",
        "benchmark_metrics": {"sharpe": held},
        **extra,
    }


def study(
    looks: list[float], kind: str = "robustness", at: str = "2026-01-02", **extra: Any
) -> dict[str, Any]:
    return {
        "created_at": at,
        "kind": kind,
        "strategy": "s",
        "symbol": "BTCUSDT",
        "interval": "1d",
        "start": "2020-01",
        "end": "2025-10",
        "looks": [{"params": {"k": i}, "sharpe": s} for i, s in enumerate(looks)],
        **extra,
    }


def test_the_funnel_counts_the_luck_bars_trials_through_each_test() -> None:
    runs = [
        run(1.2, 1.0, "2026-01-01T01"),  # positive, beats holding
        run(0.8, 1.0, "2026-01-01T02"),  # positive, behind holding
        run(-0.3, 1.0, "2026-01-01T03"),  # negative
        run(0.5, None, "2026-01-01T04"),  # nothing to compare against
        run(9.9, 1.0, "2026-01-01T05", used_holdout=True),  # a holdout run is not a trial
    ]
    studies = [study([1.1, 0.9])]  # compared with holding from the run on the same period
    f = pool_funnel(runs, studies, "main")
    assert f.trials == trial_set(runs, studies).count == 6
    assert (f.positive, f.beat_holding, f.no_holding) == (5, 2, 1)
    assert f.luck_bar == trial_set(runs, studies).expected_max_sharpe_annual
    above = [x for x in (1.2, 0.8, 0.5, 1.1, 0.9, -0.3) if x > f.luck_bar]
    assert f.above_bar == len(above) == 4  # a small pool has a low bar
    assert (f.cleared_luck, f.unscored, f.passed_own_test) == (0, 2, None)  # the two looks


def test_a_look_above_the_bar_is_unscored_never_a_pass() -> None:
    runs = [run(0.1, 0.0, f"2026-01-01T{i:02d}") for i in range(10)]
    runs.append(run(30.0, 0.0, "2026-01-02T00"))  # far above any bar: deflates past 95%
    f = pool_funnel(runs, [study([30.0])], "main")
    assert f.above_bar == 2
    assert f.cleared_luck == 1  # the run, scored exactly
    assert f.unscored == 1  # the look: no daily returns to deflate


def test_confirmation_runs_are_judged_by_their_own_test() -> None:
    passed = study(
        [0.6], kind="confirmation", confirmation=True, verdict="PASS",
        detail={"beats_share": 0.83},
    )  # fmt: skip
    failed = study(
        [0.4], kind="confirmation", at="2026-01-03", confirmation=True, verdict="FAIL",
        detail={"beats_share": 0.4},
    )  # fmt: skip
    failed["strategy"] = "other"
    f = pool_funnel([], [passed, failed], "confirmation")
    assert (f.trials, f.beat_holding, f.passed_own_test) == (2, 1, 1)
    assert pool_funnel([], [passed, failed], "main").trials == 0


def test_what_was_kept_and_the_endpoint(tmp_path: Path) -> None:
    (tmp_path / "holdout").mkdir()
    (tmp_path / "holdout" / "a.json").write_text(json.dumps({"passes": True}))
    (tmp_path / "holdout" / "b.json").write_text(json.dumps({"passes": False}))
    (tmp_path / "paper" / "tvt-btc").mkdir(parents=True)
    (tmp_path / "paper" / "export.log").write_text("")
    k = kept(["pursue", "drop", "drop"], tmp_path)
    assert (k.decisions, k.holdouts, k.holdouts_passed, k.paper_accounts) == (
        {"pursue": 1, "drop": 2}, 2, 1, 1,
    )  # fmt: skip
    assert kept([], tmp_path / "nothing").holdouts == 0
    f = funnel([run(1.0, 0.5, "2026-01-01")], [], ["pursue"], tmp_path)
    assert [p.pool for p in f.pools] == ["main", "confirmation", "forex"]
    assert f.all_trials == 1

    client = TestClient(create_app(data_dir=tmp_path, research_dir=tmp_path / "research"))
    got = client.get("/api/funnel")
    assert got.status_code == 200, got.text
    body = got.json()
    assert [p["pool"] for p in body["pools"]] == ["main", "confirmation", "forex"]
    assert body["kept"]["holdouts"] == 2
    assert body["kept"]["paper_accounts"] == 1
