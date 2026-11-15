"""The deflated Sharpe ratio: a result's Sharpe, corrected for every strategy tried.

A backtest's probabilistic Sharpe (PSR, kestrel.backtest.metrics) asks how likely the
true Sharpe is above zero, as if this were the only strategy ever tested. After many
tries the best Sharpe is high partly by luck. Bailey and Lopez de Prado (2014) estimate
how high the best of N independent trials would be by luck alone:

    SR0 = sigma_SR * ((1 - g) * Z^-1(1 - 1/N) + g * Z^-1(1 - 1/(N e)))

where sigma_SR is the standard deviation of the trials' Sharpe ratios, g is the
Euler-Mascheroni constant and Z^-1 the standard normal quantile. The deflated Sharpe is
the PSR measured against SR0 instead of zero:

    DSR = Z((SR - SR0) * sqrt(n - 1) / sqrt(1 - skew * SR + (kurtosis - 1) / 4 * SR^2))

with SR, skew and kurtosis (not excess) of daily returns and n the number of days.

What counts as a trial: every distinct configuration (strategy, parameters, market, bar
size, period, cash, note version) run on data before the holdout, research or not, and
every setting a validation study tried (kestrel.validation.log). An engine test looked
at six times is six looks. Buy-and-hold is the benchmark, not a try. Identical re-runs
count once; a study setting identical to a saved run may count twice. Trials are treated
as independent, which they aren't (many share markets and periods): correlated trials
make the true correction smaller. Both err on the strict side.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import NormalDist, stdev
from typing import Any, Literal

from kestrel.backtest.metrics import DAYS_PER_YEAR
from kestrel.backtest.runs import trial_key

EULER_GAMMA = 0.5772156649015329
_Z = NormalDist()


@dataclass(frozen=True)
class TrialSet:
    count: int
    """Distinct configurations tried."""
    sharpe_std: float
    """Standard deviation of their daily Sharpe ratios (0 with fewer than two)."""

    @property
    def expected_max_sharpe(self) -> float:
        """Daily Sharpe the best of ``count`` worthless strategies would reach by luck."""
        n = self.count
        if n < 2 or self.sharpe_std == 0:
            return 0.0
        return self.sharpe_std * (
            (1 - EULER_GAMMA) * _Z.inv_cdf(1 - 1 / n)
            + EULER_GAMMA * _Z.inv_cdf(1 - 1 / (n * math.e))
        )

    @property
    def expected_max_sharpe_annual(self) -> float:
        return self.expected_max_sharpe * math.sqrt(DAYS_PER_YEAR)


Pool = Literal["main", "confirmation"]


def pool_of(item: dict[str, Any]) -> Pool:
    """Which trial pool a run or study belongs to: confirmation runs of the exploration
    experiment (kestrel.exploration) are counted apart from everything else."""
    return "confirmation" if item.get("confirmation") else "main"


def counts_as_trial(run: dict[str, Any], pool: Pool | None = None) -> bool:
    """Whether a saved run is a trial: of its own pool, or of ``pool`` if given."""
    if pool is not None and pool_of(run) != pool:
        return False
    return run["strategy"] != "buy-and-hold" and not run.get("used_holdout", False)


def trial_set(
    runs: Iterable[dict[str, Any]],
    studies: Iterable[dict[str, Any]] = (),
    pool: Pool = "main",
) -> TrialSet:
    """The trials among saved runs (``RunStore.summaries()``) and validation studies
    (``ValidationLog.studies()``): one Sharpe per distinct configuration, the latest of
    each, within one pool (the main research, or the exploration experiment's
    confirmation runs)."""
    latest: dict[tuple[Any, ...], float] = {}
    for run in sorted(runs, key=lambda r: r["created_at"]):
        sharpe = run["metrics"].get("sharpe")
        if counts_as_trial(run, pool) and sharpe is not None:
            latest[trial_key(run)] = sharpe / math.sqrt(DAYS_PER_YEAR)
    for study in sorted(studies, key=lambda s: s["created_at"]):
        if pool_of(study) != pool:
            continue
        for look in study["looks"]:
            if look["sharpe"] is not None:
                key = (
                    "study",
                    study["strategy"],
                    tuple(sorted(look["params"].items())),
                    study["symbol"],
                    study["interval"],
                    study["start"],
                    study["end"],
                )
                latest[key] = look["sharpe"] / math.sqrt(DAYS_PER_YEAR)
    values = list(latest.values())
    return TrialSet(len(values), stdev(values) if len(values) > 1 else 0.0)


def trial_history(
    runs: Iterable[dict[str, Any]], studies: Iterable[dict[str, Any]] = ()
) -> list[tuple[datetime, TrialSet]]:
    """The trial set as it stood after each saved run or study that logged a trial,
    oldest first: how the main luck bar moved as strategies were tried. Each point is the
    same ``trial_set`` over everything logged by then, so the last one is today's bar."""
    runs, studies = list(runs), list(studies)

    def at(item: dict[str, Any]) -> datetime:
        return datetime.fromisoformat(str(item["created_at"])).astimezone(UTC)

    moments = sorted(
        {at(r) for r in runs if counts_as_trial(r, "main")}
        | {
            at(s)
            for s in studies
            if pool_of(s) == "main" and any(look["sharpe"] is not None for look in s["looks"])
        }
    )
    return [
        (m, trial_set([r for r in runs if at(r) <= m], [s for s in studies if at(s) <= m]))
        for m in moments
    ]


def deflated_sharpe(
    annual_sharpe: float | None,
    days: int,
    skew: float | None,
    kurtosis: float | None,
    trials: TrialSet,
) -> float | None:
    """Probability the true Sharpe beats the best of ``trials`` by luck; None when the
    Sharpe is undefined."""
    if annual_sharpe is None or days < 2:
        return None
    sr = annual_sharpe / math.sqrt(DAYS_PER_YEAR)
    g3 = skew or 0.0
    g4 = kurtosis or 3.0
    variance = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    if variance <= 0:
        return None
    return _Z.cdf((sr - trials.expected_max_sharpe) * math.sqrt(days - 1) / math.sqrt(variance))
