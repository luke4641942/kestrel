"""The scorecard: each test graded from a stored run and its studies."""

from __future__ import annotations

from typing import Any

import pytest

from kestrel.validation import rules
from kestrel.validation.scorecard import grade

PARAMS = {"fast": 20, "slow": 120}


def run(**over: Any) -> dict[str, Any]:
    """A run passing every test that needs no study."""
    base: dict[str, Any] = {
        "strategy": "trend",
        "symbol": "BTCUSDT",
        "interval": "1d",
        "start": "2020-01",
        "end": "2025-10",
        "params": PARAMS,
        "used_holdout": False,
        "metrics": {"sharpe": 1.5, "total_return": 6.4, "max_drawdown": -0.3, "trades": 500},
        "benchmark_metrics": {"sharpe": 1.0},
        "analysis": {
            "matched_btc_return": 4.2,
            "cost_per_round_trip_bps": 22.0,
            "break_even_cost_bps": 288.0,
            "start_dates": [
                {"window_days": 365, "windows": 60, "sharpe": 0.1},
                {"window_days": 730, "windows": 48, "sharpe": 0.6},
            ],
        },
    }
    for k, v in over.items():
        base[k] = {**base[k], **v} if isinstance(v, dict) and k != "params" else v
    return base


def walk_forward(wins: int | None, folds: int = 7, **over: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {"folds": folds}
    if wins is not None:
        detail["windows_beating_holding"] = wins
    return {
        "kind": "walk-forward", "strategy": "trend", "symbol": "BTCUSDT", "interval": "1d",
        "start": "2020-01", "end": "2025-10", "created_at": "2026-11-12T00:00:00",
        "looks": [], "detail": detail, **over,
    }  # fmt: skip


def robustness(neighbours: list[float | None], center: dict[str, int] = PARAMS) -> dict[str, Any]:
    looks = [{"params": center, "sharpe": 1.5}] + [
        {"params": {"fast": 20 + i, "slow": 120}, "sharpe": s} for i, s in enumerate(neighbours, 1)
    ]
    return {
        "kind": "robustness", "strategy": "trend", "symbol": "BTCUSDT", "interval": "1d",
        "start": "2020-01", "end": "2025-10", "created_at": "2026-11-12T00:00:00",
        "looks": looks, "detail": {"center": center},
    }  # fmt: skip


def statuses(card: Any) -> dict[str, str]:
    return {g.name: g.status for g in card.grades}


def test_all_nine_tests_in_the_rules_order() -> None:
    card = grade(run(), 0.97, [walk_forward(5), robustness([1.2] * 10)])
    assert card is not None
    assert [(g.name, g.rule) for g in card.grades] == [(t.name, t.rule) for t in rules.SCORECARD]
    assert card.verdict == "pass"
    assert (card.failed, card.not_run) == (0, 0)


def test_measured_values_are_stated() -> None:
    card = grade(run(), 0.28, [walk_forward(5), robustness([1.2] * 9 + [0.5])])
    assert card is not None
    measured = {g.name: g.measured for g in card.grades}
    assert measured["Edge"] == (
        "Sharpe 1.50 vs 1.00 for holding; return +640% vs +420% at the same exposure"
    )
    assert measured["Luck"] == "Deflated Sharpe 28%"
    assert measured["Out of sample"] == "Beat holding in 5 of 7 test windows"
    assert measured["Start dates"] == "Better Sharpe than holding in 60% of 48 windows"
    assert measured["Costs"] == "Break-even 288 bp vs 22 bp a round trip (13.1x)"
    assert measured["Enough trades"] == "500 trades"
    assert measured["Drawdown limit"] == "Worst drawdown -30%"
    assert measured["Robustness"] == "90% of 10 neighbouring settings beat holding (80% needed)"
    assert card.verdict == "fail"  # the luck test alone fails it


@pytest.mark.parametrize(
    ("over", "deflated", "failing"),
    [
        ({"metrics": {"sharpe": 0.9}}, 0.97, "Edge"),
        ({"metrics": {"sharpe": 1.0}}, 0.97, "Edge"),  # level with holding isn't beating it
        ({}, 0.949, "Luck"),
        ({"analysis": {"start_dates": [{"window_days": 730, "windows": 9, "sharpe": 0.4}]}},
         0.97, "Start dates"),
        ({"analysis": {"break_even_cost_bps": 43.0}}, 0.97, "Costs"),
        ({"analysis": {"break_even_cost_bps": None}}, 0.97, "Costs"),  # lost money before costs
        ({"metrics": {"trades": 29}}, 0.97, "Enough trades"),
        ({"metrics": {"max_drawdown": -0.51}}, 0.97, "Drawdown limit"),
    ],
)  # fmt: skip
def test_each_threshold_fails_on_its_own(
    over: dict[str, Any], deflated: float, failing: str
) -> None:
    card = grade(run(**over), deflated, [walk_forward(5), robustness([1.2] * 10)])
    assert card is not None
    assert [n for n, s in statuses(card).items() if s == "fail"] == [failing]
    assert card.verdict == "fail"


def test_boundaries_pass() -> None:
    over = {
        "metrics": {"trades": 30, "max_drawdown": -0.5},
        "analysis": {
            "break_even_cost_bps": 44.0,
            "start_dates": [{"window_days": 730, "windows": 10, "sharpe": 0.5}],
        },
    }
    card = grade(run(**over), 0.95, [walk_forward(4), robustness([1.2] * 8 + [0.5] * 2)])
    assert card is not None
    assert card.verdict == "pass"


def test_round_trips_count_when_a_strategy_has_them() -> None:
    card = grade(run(metrics={"trades": 400, "round_trips": 12}), 0.97, [])
    assert card is not None
    assert statuses(card)["Enough trades"] == "fail"


def test_walk_forward_needs_most_windows() -> None:
    def status(study: dict[str, Any]) -> str:
        card = grade(run(), 0.97, [study])
        assert card is not None
        return statuses(card)["Out of sample"]

    assert status(walk_forward(4, folds=8)) == "fail"  # half isn't most
    assert status(walk_forward(5, folds=8)) == "pass"
    assert status(walk_forward(None)) == "not run"  # saved before the comparison existed
    assert status(walk_forward(5, symbol="ETHUSDT")) == "not run"  # another market
    assert status(walk_forward(5, end="2024-10")) == "not run"  # another period
    newest = walk_forward(1, created_at="2026-11-14T00:00:00")
    card = grade(run(), 0.97, [walk_forward(6), newest])
    assert card is not None
    assert statuses(card)["Out of sample"] == "fail"  # the newest study counts


def test_robustness_compares_neighbours_with_holding() -> None:
    def status(study: dict[str, Any]) -> str:
        card = grade(run(), 0.97, [study])
        assert card is not None
        return statuses(card)["Robustness"]

    assert status(robustness([1.2] * 7 + [0.9] * 3)) == "fail"  # 70% beat holding
    assert status(robustness([1.2] * 8 + [None, 0.9, 0.9])) == "pass"  # None isn't counted
    assert status(robustness([1.2] * 10, center={"fast": 10, "slow": 120})) == "not run"
    assert status(robustness([])) == "not run"


def test_missing_evidence_is_not_run_not_failed() -> None:
    bare = run(analysis=None)
    bare["analysis"] = None
    card = grade(bare, None, [])
    assert card is not None
    assert statuses(card) == {
        "Edge": "pass",
        "Luck": "not run",
        "Out of sample": "not run",
        "Start dates": "not run",
        "Costs": "not run",
        "Enough trades": "pass",
        "Drawdown limit": "pass",
        "Robustness": "not run",
        "Random entry": "n/a",
    }
    assert card.verdict == "incomplete"
    assert card.not_run == 5
    never = grade(run(metrics={"sharpe": None}), None, [])
    assert never is not None
    assert statuses(never)["Edge"] == "not run"


def test_a_given_analysis_overrides_the_stored_one() -> None:
    fresh = {"start_dates": [{"window_days": 730, "windows": 10, "sharpe": 0.2}]}
    card = grade(run(), 0.97, [], analysis=fresh)
    assert card is not None
    assert statuses(card)["Start dates"] == "fail"


def test_benchmark_and_holdout_runs_are_not_graded() -> None:
    assert grade(run(strategy="buy-and-hold"), None, []) is None
    assert grade(run(used_holdout=True), None, []) is None


def test_robustness_does_not_apply_to_a_strategy_with_no_settings() -> None:
    card = grade(run(params={}), 0.97, [walk_forward(5)])
    assert card is not None
    robust = next(g for g in card.grades if g.name == "Robustness")
    assert (robust.status, robust.measured) == (
        "n/a",
        "No settings to tune, so none could be fitted to the past.",
    )
    assert card.verdict == "pass"  # n/a doesn't hold a pass back
    assert card.not_run == 0


def control(beaten: float | None, params: dict[str, int] = PARAMS, **over: Any) -> dict[str, Any]:
    return {
        "kind": "random-entry", "strategy": "trend", "symbol": "BTCUSDT", "interval": "1d",
        "start": "2020-01", "end": "2025-10", "created_at": "2026-11-15T00:00:00",
        "looks": [], "detail": {"params": params, "paths": 200, "beaten_share": beaten}, **over,
    }  # fmt: skip


def test_random_entry_applies_only_to_bracket_strategies() -> None:
    def card_for(style: str, studies: list[dict[str, Any]]) -> Any:
        card = grade(run(style=style), 0.97, [walk_forward(5), robustness([1.2] * 10), *studies])
        assert card is not None
        return next(g for g in card.grades if g.name == "Random entry")

    weights = card_for("weights", [control(0.2)])
    assert (weights.status, weights.measured) == (
        "n/a",
        "Holds weights, not brackets: no payoff shape to copy.",
    )
    timing = {**control(0.97), "kind": "random-timing"}
    shown = card_for("weights", [timing])
    assert shown.status == "n/a"  # information only: never a pass or a fail
    assert "beat 97% of 200 copies of its own weights at random times" in shown.measured
    other = {**control(0.97, {"fast": 5, "slow": 120}), "kind": "random-timing"}
    assert "random-timing" not in card_for("weights", [other]).measured  # other settings
    assert card_for("brackets", []).status == "not run"
    assert card_for("brackets", [control(0.99, {"fast": 5, "slow": 120})]).status == "not run"
    passed = card_for("brackets", [control(0.95)])
    assert (passed.status, passed.measured) == ("pass", "Beat 95% of 200 random-entry paths")
    assert card_for("brackets", [control(0.94)]).status == "fail"
    assert card_for("brackets", [control(None)]).status == "fail"  # it never traded
