"""The deflated Sharpe ratio and the trial set it corrects for."""

from __future__ import annotations

import itertools
import math
from statistics import NormalDist
from typing import Any

import pytest

from kestrel.validation.deflated import TrialSet, counts_as_trial, deflated_sharpe, trial_set

DAY = math.sqrt(365)


def run(
    strategy: str = "ma-trend", sharpe: float | None = 1.0, created: str = "2026-01-01", **over: Any
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "created_at": created,
        "params": {},
        "research": None,
        "symbol": "BTCUSDT",
        "interval": "4h",
        "start": "2020-01",
        "end": "2025-10",
        "initial_cash": "330",
        "used_holdout": False,
        "metrics": {"sharpe": sharpe},
        **over,
    }


def test_the_best_of_a_thousand_matches_the_paper() -> None:
    # Bailey and Lopez de Prado (2014): with a Sharpe spread of 1, the expected best of
    # 1,000 independent trials is about 3.26.
    assert TrialSet(1000, 1.0).expected_max_sharpe == pytest.approx(3.26, abs=0.01)


def test_one_trial_has_nothing_to_correct_for() -> None:
    assert TrialSet(1, 0.5).expected_max_sharpe == 0
    assert TrialSet(5, 0.0).expected_max_sharpe == 0
    # So the deflated Sharpe equals the probabilistic Sharpe: P(true SR > 0).
    sr = 1.0 / DAY
    psr = NormalDist().cdf(sr * math.sqrt(999) / math.sqrt(1 + 0.5 * sr**2))
    assert deflated_sharpe(1.0, 1000, 0.0, 3.0, TrialSet(1, 0.0)) == pytest.approx(psr)


def test_more_trials_deflate_more() -> None:
    values = [deflated_sharpe(1.2, 2000, 0.0, 3.0, TrialSet(n, 0.7 / DAY)) for n in (1, 5, 30, 300)]
    assert all(a is not None and b is not None and a > b for a, b in itertools.pairwise(values))
    assert TrialSet(30, 0.7 / DAY).expected_max_sharpe_annual == pytest.approx(1.47, abs=0.02)


def test_undefined_inputs_have_no_deflated_sharpe() -> None:
    trials = TrialSet(10, 0.01)
    assert deflated_sharpe(None, 1000, 0.0, 3.0, trials) is None
    assert deflated_sharpe(1.0, 1, 0.0, 3.0, trials) is None
    assert deflated_sharpe(30.0, 1000, 50.0, 3.0, trials) is None  # variance term not positive
    assert deflated_sharpe(1.0, 1000, None, None, trials) is not None  # normal by default


def test_what_counts_as_a_trial() -> None:
    assert counts_as_trial(run())
    assert not counts_as_trial(run("buy-and-hold"))  # the benchmark is not a try
    assert not counts_as_trial(run(used_holdout=True))  # nor is the one final test


def test_the_trial_set_counts_distinct_configurations_once() -> None:
    runs = [
        run(sharpe=0.2, created="2026-01-01"),
        run(sharpe=0.4, created="2026-01-02"),  # identical rerun: counts once, latest wins
        run(sharpe=1.0, params={"fast": 10}),
        run(sharpe=None, params={"fast": 11}),  # flat equity: no Sharpe to count
        run("buy-and-hold", sharpe=5.0),
        run(sharpe=9.0, used_holdout=True),
    ]
    t = trial_set(runs)
    assert t.count == 2
    assert t.sharpe_std * DAY == pytest.approx(math.sqrt(2) * 0.3)  # stdev of 0.4 and 1.0
    assert trial_set([run()]).sharpe_std == 0


def test_the_history_replays_the_trial_set_after_each_trial() -> None:
    from datetime import UTC, datetime

    from kestrel.validation.deflated import trial_history

    runs = [
        run(sharpe=1.0, created="2026-01-01T00:00:00+00:00", params={"a": 1}),
        run(sharpe=-2.0, created="2026-01-02T00:00:00+00:00", params={"a": 2}),
        run("buy-and-hold", sharpe=0.9, created="2026-01-03T00:00:00+00:00"),  # not a trial
        run(sharpe=0.5, created="2026-01-04T00:00:00+00:00", params={"a": 3}),
    ]
    study = {
        "created_at": "2026-01-05T00:00:00+00:00",
        "strategy": "ma-trend",
        "symbol": "ETHUSDT",
        "interval": "1d",
        "start": "2020-01",
        "end": "2025-10",
        "looks": [{"params": {"b": 1}, "sharpe": 0.1}, {"params": {"b": 2}, "sharpe": None}],
    }
    history = trial_history(runs, [study])
    assert [m for m, _ in history] == [
        datetime(2026, 1, d, tzinfo=UTC) for d in (1, 2, 4, 5)
    ]  # the benchmark run adds no point
    assert [t.count for _, t in history] == [1, 2, 3, 4]
    assert history[-1][1] == trial_set(runs, [study])
    assert history[1][1] == trial_set(runs[:2])
    assert trial_history([], []) == []
