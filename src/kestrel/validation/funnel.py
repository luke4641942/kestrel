"""The funnel: every trial in a pool, through the scorecard's first tests and the luck test,
then what was kept. Read from the trial log and the records; nothing is run.

Trials (``kestrel.validation.deflated.trials``, the same ones the luck bar counts):

1. every distinct configuration tried;
2. positive after costs (every Sharpe here is after costs);
3. beat holding the same market over the same period on Sharpe (test 1; holding less of a
   coin has the same Sharpe as holding all of it, so this is exposure-matched holding). A
   study's look is compared with holding's Sharpe from a saved run on the same market and
   period; a confirmation run (one look across many coins) with its own measure, beating
   holding on more than half of its coins. Forex runs have no holding benchmark: they go
   long and short. Trials with nothing to compare against are counted, not guessed;
4. above the pool's luck bar (the Sharpe the best of that many worthless trials would reach);
5. cleared the luck test (deflated Sharpe at least 95%, test 2). A saved run is scored
   exactly. A study's look has no daily returns stored, so it can't be: one at or below the
   bar fails for certain (its deflated Sharpe is under 50%); one above it is counted as
   unscored, never as a pass.

Then what became of them, per strategy and market (``research/decisions.toml``), the
holdout records and the paper accounts. Pursue means carry on to paper, not passed: every
pursued strategy fails the luck test, and the funnel shows it.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kestrel.validation.deflated import (
    POOLS,
    Pool,
    Trial,
    deflated_sharpe,
    trial_set,
    trials,
)

LUCK_PASS = 0.95


@dataclass(frozen=True)
class PoolFunnel:
    pool: Pool
    trials: int
    positive: int
    beat_holding: int
    no_holding: int
    """Trials with no holding comparison stored (counted, not guessed)."""
    above_bar: int
    luck_bar: float
    cleared_luck: int
    unscored: int
    """Study looks above the bar, whose deflated Sharpe can't be computed."""
    passed_own_test: int | None = None
    """Confirmation runs only: how many passed their frozen note's own test (the pool's two
    trials make its luck bar near zero, so that test is what judged them)."""


@dataclass(frozen=True)
class Kept:
    decisions: dict[str, int]
    """Strategy-market decisions by kind: pursue, watch, needs-shorting, drop."""
    holdouts: int
    holdouts_passed: int
    paper_accounts: int


@dataclass(frozen=True)
class Funnel:
    pools: list[PoolFunnel]
    kept: Kept

    @property
    def all_trials(self) -> int:
        return sum(p.trials for p in self.pools)


def _holding(trial: Trial, by_period: dict[tuple[Any, ...], float | None]) -> bool | None:
    """Did the trial beat holding? None when there is nothing to compare against."""
    item = trial.item
    if trial.look is None:
        held = (item.get("benchmark_metrics") or {}).get("sharpe")
        return None if held is None else trial.sharpe > held
    if item.get("kind") == "confirmation":
        share = (item.get("detail") or {}).get("beats_share")
        return None if share is None else share > 0.5
    held = by_period.get((item["symbol"], item["interval"], item["start"], item["end"]))
    return None if held is None else trial.sharpe > held


def pool_funnel(
    runs: list[dict[str, Any]], studies: list[dict[str, Any]], pool: Pool
) -> PoolFunnel:
    ts = trial_set(runs, studies, pool)
    bar = ts.expected_max_sharpe_annual
    by_period: dict[tuple[Any, ...], float | None] = {}
    for run in sorted(runs, key=lambda r: r["created_at"]):  # the latest run per period
        if "benchmark_metrics" in run:
            key = (run["symbol"], run["interval"], run["start"], run["end"])
            by_period[key] = (run["benchmark_metrics"] or {}).get("sharpe")
    found = trials(runs, studies, pool)
    beat = [_holding(t, by_period) for t in found]
    cleared = unscored = 0
    for t in found:
        if t.sharpe <= bar:
            continue
        if t.look is not None:
            unscored += 1
            continue
        m = t.item["metrics"]
        dsr = deflated_sharpe(m["sharpe"], m["days"], m.get("skew"), m.get("kurtosis"), ts)
        cleared += dsr is not None and dsr >= LUCK_PASS
    return PoolFunnel(
        pool=pool,
        trials=len(found),
        positive=sum(t.sharpe > 0 for t in found),
        beat_holding=sum(b is True for b in beat),
        no_holding=sum(b is None for b in beat),
        above_bar=sum(t.sharpe > bar for t in found),
        luck_bar=bar,
        cleared_luck=cleared,
        unscored=unscored,
        passed_own_test=(
            sum(t.item.get("verdict") == "PASS" for t in found) if pool == "confirmation" else None
        ),
    )


def kept(decisions: Iterable[str], data_dir: Path | str) -> Kept:
    """What was kept: decision kinds, holdout records (``data/holdout``), paper accounts
    (``data/paper/<account>/``)."""
    root = Path(data_dir)
    records = [json.loads(p.read_text()) for p in sorted((root / "holdout").glob("*.json"))]
    paper = root / "paper"
    accounts = [p for p in paper.iterdir() if p.is_dir()] if paper.is_dir() else []
    return Kept(
        decisions=dict(Counter(decisions)),
        holdouts=len(records),
        holdouts_passed=sum(bool(r.get("passes")) for r in records),
        paper_accounts=len(accounts),
    )


def funnel(
    runs: Iterable[dict[str, Any]],
    studies: Iterable[dict[str, Any]],
    decisions: Iterable[str],
    data_dir: Path | str,
) -> Funnel:
    runs, studies = list(runs), list(studies)
    return Funnel([pool_funnel(runs, studies, p) for p in POOLS], kept(decisions, data_dir))
