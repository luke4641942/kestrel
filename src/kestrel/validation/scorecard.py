"""Grading a saved run against the scorecard (kestrel.validation.rules).

Each of the nine tests comes out pass, fail, not run or n/a (robustness, for a strategy
with no settings to tune; random entry, for one holding weights rather than brackets), with
what was measured. A run passes only when all nine pass
or don't apply; one failure fails it; otherwise it is incomplete (a
validation study is missing). Everything is read from what is already stored: the run's
metrics and analysis, its deflated Sharpe, and the validation studies on the same
strategy, market and period.

Tests 1, 3, 4 and 8 compare with holding the same coin at the strategy's average exposure.
Sharpe doesn't change with position size: holding half a coin, rebalanced daily, earns
exactly half the coin's daily returns, so it has the coin's Sharpe. Beating holding's
Sharpe is therefore beating exposure-matched holding, and the stored benchmark (and
each study's holding comparison) can be used as it is. The exposure-matched *return* is
shown beside test 1 as context.

Runs on the holdout, and buy-and-hold itself, aren't graded: the scorecard is for
strategies on data before the holdout.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from kestrel.validation import rules
from kestrel.validation.deflated import counts_as_trial

Status = Literal["pass", "fail", "not run", "n/a"]
Verdict = Literal["pass", "fail", "incomplete"]

START_WINDOW_DAYS = 730
ROBUST_SHARE = 0.8
"""Share of neighbouring settings that must also beat holding (as kestrel.validation.robustness)."""


@dataclass(frozen=True)
class Grade:
    name: str
    rule: str
    status: Status
    measured: str
    """What was measured, or why the test couldn't run."""


@dataclass(frozen=True)
class Scorecard:
    grades: list[Grade]

    @property
    def verdict(self) -> Verdict:
        statuses = [g.status for g in self.grades]
        if "fail" in statuses:
            return "fail"
        return "pass" if all(s in ("pass", "n/a") for s in statuses) else "incomplete"

    @property
    def failed(self) -> int:
        return sum(g.status == "fail" for g in self.grades)

    @property
    def not_run(self) -> int:
        return sum(g.status == "not run" for g in self.grades)


def _pct(x: float) -> str:
    return f"{x:+.0%}"


def _same_market(run: dict[str, Any], study: dict[str, Any]) -> bool:
    return all(run[k] == study[k] for k in ("strategy", "symbol", "interval", "start", "end"))


def _latest(
    studies: Iterable[dict[str, Any]], kind: str, run: dict[str, Any]
) -> dict[str, Any] | None:
    found = [s for s in studies if s["kind"] == kind and _same_market(run, s)]
    return max(found, key=lambda s: s["created_at"]) if found else None


def _status(ok: bool) -> Status:
    return "pass" if ok else "fail"


def _edge(sharpe: float | None, held: float | None, matched: float | None, own: float) -> Grade:
    rule = rules.SCORECARD[0]
    if sharpe is None or held is None:
        return Grade(rule.name, rule.rule, "not run", "No Sharpe: the run never traded.")
    measured = f"Sharpe {sharpe:.2f} vs {held:.2f} for holding"
    if matched is not None:
        measured += f"; return {_pct(own)} vs {_pct(matched)} at the same exposure"
    return Grade(rule.name, rule.rule, _status(sharpe > held), measured)


def _luck(deflated: float | None) -> Grade:
    rule = rules.SCORECARD[1]
    if deflated is None:
        return Grade(rule.name, rule.rule, "not run", "No deflated Sharpe for this run.")
    return Grade(
        rule.name,
        rule.rule,
        _status(deflated >= rules.DEFLATED_SHARPE_MIN),
        f"Deflated Sharpe {deflated:.0%}",
    )


def _walk_forward(study: dict[str, Any] | None) -> Grade:
    rule = rules.SCORECARD[2]
    if study is None:
        return Grade(rule.name, rule.rule, "not run", "No walk-forward on this market and period.")
    detail = study["detail"]
    wins, folds = detail.get("windows_beating_holding"), detail.get("folds")
    if wins is None or not folds:
        return Grade(
            rule.name,
            rule.rule,
            "not run",
            "The walk-forward predates the per-window comparison; rerun it (identical "
            "settings add no trials).",
        )
    return Grade(
        rule.name,
        rule.rule,
        _status(wins > folds / 2),
        f"Beat holding in {wins} of {folds} test windows",
    )


def _start_dates(analysis: dict[str, Any] | None) -> Grade:
    rule = rules.SCORECARD[3]
    rows = (analysis or {}).get("start_dates") or []
    row = next((r for r in rows if r["window_days"] == START_WINDOW_DAYS), None)
    if row is None or not row["windows"] or row["sharpe"] is None:
        return Grade(rule.name, rule.rule, "not run", "The run is too short for 2-year windows.")
    share = row["sharpe"]
    return Grade(
        rule.name,
        rule.rule,
        _status(share >= rules.START_WINDOW_SHARE),
        f"Better Sharpe than holding in {share:.0%} of {row['windows']} windows",
    )


def _costs(analysis: dict[str, Any] | None) -> Grade:
    rule = rules.SCORECARD[4]
    a = analysis or {}
    actual, even = a.get("cost_per_round_trip_bps"), a.get("break_even_cost_bps")
    if not actual:
        return Grade(rule.name, rule.rule, "not run", "No completed round trips to cost.")
    if even is None or even <= 0:
        return Grade(rule.name, rule.rule, "fail", "No profit before costs to absorb them")
    return Grade(
        rule.name,
        rule.rule,
        _status(even >= rules.BREAK_EVEN_MULTIPLE * actual),
        f"Break-even {even:.0f} bp vs {actual:.0f} bp a round trip ({even / actual:.1f}x)",
    )


def _trades(metrics: dict[str, Any]) -> Grade:
    rule = rules.SCORECARD[5]
    count = metrics.get("round_trips")
    count = metrics["trades"] if count is None else count
    return Grade(rule.name, rule.rule, _status(count >= rules.MIN_TRADES), f"{count} trades")


def _drawdown(metrics: dict[str, Any]) -> Grade:
    rule = rules.SCORECARD[6]
    dd = metrics["max_drawdown"]
    return Grade(
        rule.name, rule.rule, _status(dd >= -rules.MAX_DRAWDOWN), f"Worst drawdown {dd:.0%}"
    )


def _robustness(study: dict[str, Any] | None, params: dict[str, Any], held: float | None) -> Grade:
    rule = rules.SCORECARD[7]
    if not params:
        return Grade(
            rule.name, rule.rule, "n/a", "No settings to tune, so none could be fitted to the past."
        )
    if study is None or study["detail"].get("center") != params:
        return Grade(rule.name, rule.rule, "not run", "No robustness check around these settings.")
    if held is None:
        return Grade(rule.name, rule.rule, "not run", "No holding Sharpe to compare with.")
    neighbours = [
        look["sharpe"]
        for look in study["looks"]
        if look["params"] != params and look["sharpe"] is not None
    ]
    if not neighbours:
        return Grade(rule.name, rule.rule, "not run", "The check found no neighbouring settings.")
    share = sum(s > held for s in neighbours) / len(neighbours)
    return Grade(
        rule.name,
        rule.rule,
        _status(share >= ROBUST_SHARE),
        f"{share:.0%} of {len(neighbours)} neighbouring settings beat holding "
        f"({ROBUST_SHARE:.0%} needed)",
    )


def _random_entry(
    study: dict[str, Any] | None, run: dict[str, Any], timing: dict[str, Any] | None = None
) -> Grade:
    rule = rules.SCORECARD[8]
    if run.get("style") != "brackets":
        said = "Holds weights, not brackets: no payoff shape to copy."
        params = run.get("params") or {}
        if timing is not None and (timing["detail"].get("params") or {}) == params:
            d = timing["detail"]
            said += (
                f" Information (random-timing control, not a test): beat "
                f"{d['beaten_share']:.0%} of {d['paths']} copies of its own weights at random "
                f"times ({rules.RANDOM_BEAT_SHARE:.0%} would say its timing beats chance)."
            )
        return Grade(rule.name, rule.rule, "n/a", said)
    params = run.get("params") or {}
    if study is None or (study["detail"].get("params") or {}) != params:
        return Grade(rule.name, rule.rule, "not run", "No random-entry control for these settings.")
    share = study["detail"].get("beaten_share")
    if share is None:
        return Grade(rule.name, rule.rule, "fail", "No trades to compare with random entries.")
    return Grade(
        rule.name,
        rule.rule,
        _status(share >= rules.RANDOM_BEAT_SHARE),
        f"Beat {share:.0%} of {study['detail']['paths']} random-entry paths",
    )


def grade(
    run: dict[str, Any],
    deflated: float | None,
    studies: Iterable[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
) -> Scorecard | None:
    """Grade a saved run (``RunStore`` meta). ``analysis`` overrides the stored one (the API
    fills in start dates for older runs). None for runs the scorecard doesn't apply to."""
    if not counts_as_trial(run):
        return None
    studies = list(studies)
    metrics, bench = run["metrics"], run["benchmark_metrics"]
    analysis = analysis if analysis is not None else run.get("analysis")
    held = bench.get("sharpe")
    return Scorecard(
        [
            _edge(
                metrics.get("sharpe"),
                held,
                (analysis or {}).get("matched_btc_return"),
                metrics["total_return"],
            ),
            _luck(deflated),
            _walk_forward(_latest(studies, "walk-forward", run)),
            _start_dates(analysis),
            _costs(analysis),
            _trades(metrics),
            _drawdown(metrics),
            _robustness(_latest(studies, "robustness", run), run.get("params") or {}, held),
            _random_entry(
                _latest(studies, "random-entry", run), run, _latest(studies, "random-timing", run)
            ),
        ]
    )
