"""Parameter robustness: is a result a plateau or a lucky peak?

A real edge should survive nearby settings: a 20-day lookback that works should mostly
work at 15 and 25 days too. A result that only appears at one exact value, with its
neighbours flat or losing, was probably found by luck. So, starting from a setting (by
default the strategy's pre-registered defaults), each parameter in turn is moved to
``steps`` times its value (default 0.5, 0.75, 1.25, 1.5), rounded and kept inside its
allowed range, with the others held. Every setting runs on the same data with the same
costs.

Verdict, fixed in advance:

- NO EDGE if the starting setting's Sharpe isn't positive: nothing to be robust about.
- ROBUST if at least 80% of the neighbouring settings keep a positive Sharpe and their
  median Sharpe is at least half the starting one.
- FRAGILE otherwise.

Every setting is a look at the data, so each is saved as a trial (kestrel.validation.log)
and counted by the deflated Sharpe.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median

from kestrel.backtest.metrics import Metrics
from kestrel.strategy import Param

STEPS = (0.5, 0.75, 1.25, 1.5)
MIN_POSITIVE_SHARE = 0.8
MIN_MEDIAN_RATIO = 0.5


@dataclass(frozen=True)
class Setting:
    moved: str | None
    """The parameter moved from the starting setting; None for the starting setting."""
    params: dict[str, int]
    sharpe: float | None
    total_return: float | None
    max_drawdown: float | None
    expectancy_r: float | None
    trades: int | None
    error: str | None = None
    """Why the setting couldn't run (e.g. a fast lookback above the slow one)."""


@dataclass(frozen=True)
class Robustness:
    strategy: str
    center: Setting
    neighbours: list[Setting]

    @property
    def scored(self) -> list[float]:
        return [s.sharpe for s in self.neighbours if s.sharpe is not None]

    @property
    def positive_share(self) -> float | None:
        scored = self.scored
        return sum(x > 0 for x in scored) / len(scored) if scored else None

    @property
    def median_sharpe(self) -> float | None:
        return median(self.scored) if self.scored else None

    @property
    def verdict(self) -> str:
        center = self.center.sharpe
        share, mid = self.positive_share, self.median_sharpe
        if center is None or center <= 0:
            return "NO EDGE"
        if (
            share is not None
            and mid is not None
            and share >= MIN_POSITIVE_SHARE
            and mid >= MIN_MEDIAN_RATIO * center
        ):
            return "ROBUST"
        return "FRAGILE"

    @property
    def is_peak(self) -> bool:
        """The starting setting beats every neighbour."""
        center = self.center.sharpe
        return center is not None and bool(self.scored) and all(center > x for x in self.scored)


def neighbourhood(
    params: tuple[Param, ...], center: dict[str, int], steps: tuple[float, ...] = STEPS
) -> list[tuple[str, dict[str, int]]]:
    """Each parameter moved to ``steps`` times its value, one at a time, within range."""
    out: list[tuple[str, dict[str, int]]] = []
    for p in params:
        seen = {center[p.name]}
        for step in steps:
            value = min(p.high, max(p.low, math.floor(center[p.name] * step + 0.5)))
            if value not in seen:
                seen.add(value)
                out.append((p.name, {**center, p.name: value}))
    return out


def check_robustness(
    strategy: str,
    params: tuple[Param, ...],
    center: dict[str, int],
    evaluate: Callable[[dict[str, int]], Metrics],
    steps: tuple[float, ...] = STEPS,
) -> Robustness:
    """Run ``evaluate`` at ``center`` and at every neighbour."""

    def run(moved: str | None, setting: dict[str, int]) -> Setting:
        try:
            m = evaluate(setting)
        except ValueError as e:
            return Setting(moved, setting, None, None, None, None, None, error=str(e))
        sharpe = None if math.isnan(m.sharpe) else m.sharpe
        trades = m.round_trips if m.round_trips is not None else m.trades
        return Setting(
            moved, setting, sharpe, m.total_return, m.max_drawdown, m.expectancy_r, trades
        )

    return Robustness(
        strategy,
        run(None, center),
        [run(name, setting) for name, setting in neighbourhood(params, center, steps)],
    )


def format_robustness(r: Robustness, market: str) -> str:
    def num(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.2f}"

    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:+.1%}"

    def row(s: Setting, label: str) -> str:
        if s.error:
            return f"  {label:<28} could not run: {s.error}"
        expectancy = "" if s.expectancy_r is None else f"{s.expectancy_r:+.3f}R"
        return (
            f"  {label:<28} {num(s.sharpe):>7} {pct(s.total_return):>9} "
            f"{pct(s.max_drawdown):>9} {s.trades or 0:>7} {expectancy:>9}"
        )

    lines = [
        f"Parameter robustness  ·  {r.strategy}  ·  {market}",
        "Starting setting: " + " ".join(f"{k}={v}" for k, v in r.center.params.items()),
        "",
        f"  {'setting':<28} {'Sharpe':>7} {'return':>9} {'max DD':>9} {'trades':>7} "
        f"{'per trade':>9}",
        row(r.center, "starting"),
    ]
    for s in r.neighbours:
        lines.append(row(s, f"{s.moved}={s.params[s.moved or '']}"))
    share, mid = r.positive_share, r.median_sharpe
    verdicts = {
        "NO EDGE": "the starting setting's Sharpe isn't positive, so there is nothing to be "
        "robust about.",
        "ROBUST": "nearby settings keep most of the result: a plateau, not a peak.",
        "FRAGILE": "nearby settings lose most of the result: it depends on the exact values.",
    }
    lines += [
        "",
        "  Neighbours with a positive Sharpe   "
        + ("n/a" if share is None else f"{share:.0%} (needs 80%)"),
        f"  Median neighbour Sharpe              {num(mid)}"
        + (
            f" ({mid / r.center.sharpe:.0%} of the starting one, needs 50%)"
            if mid is not None and r.center.sharpe
            else ""
        ),
        f"  Verdict: {r.verdict}: {verdicts[r.verdict]}",
    ]
    if r.is_peak and r.verdict != "NO EDGE":
        lines.append(
            "  The starting setting beats every neighbour: treat its exact result as a peak."
        )
    lines.append(f"  Every setting is a trial: {1 + len(r.neighbours)} saved to the trial log.")
    return "\n".join(lines)
