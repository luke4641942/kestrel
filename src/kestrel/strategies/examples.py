"""Worked examples of a strategy's rule, for checking against its note's wording before the
note's first run (the strategy counterpart of ``kestrel signal-examples``).

A few of the rule's entries and exits drawn at random (seeded, so never picked by eye),
each with what decided it: the target weight the strategy chose at that close, and, for a
strategy that can explain itself (``waiting``, the tests the paper accounts show), its tests
at that close with their values. The two are computed separately, so an example whose tests
and weight disagree is a bug, flagged.

Exploration-half coins only, before the holdout: a chart shows what came after each
decision, and nothing on the exploration half is evidence (docs/rules.md), so looking there
spends nothing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime

import polars as pl

from kestrel.exploration import Split
from kestrel.strategies.conditions import Condition
from kestrel.strategy import Strategy, validated_weights

COUNT = 3
SEED = 0


@dataclass(frozen=True)
class Example:
    open_time: datetime
    """The bar whose close made the decision; it trades at the next bar's open."""
    kind: str
    """"entry" (from cash into the market) or "exit" (back to cash)."""
    before: float
    after: float
    close: float
    rule: str | None
    """"all" or "any" when the strategy explains itself, else None."""
    conditions: list[Condition]

    @property
    def agrees(self) -> bool | None:
        """Do the tests say what the weight did? None when there are no tests to read."""
        if self.rule is None or not self.conditions:
            return None
        met = [c.met for c in self.conditions]
        held = all(met) if self.rule == "all" else any(met)
        return held == (self.after > 0)


def require_exploration(symbol: str, split: Split | None) -> None:
    if split is None or symbol.upper() not in split.exploration:
        raise ValueError(
            f"worked examples use exploration-half coins only (a chart shows what came "
            f"after each decision, and only that half is never evidence); {symbol.upper()} "
            "isn't one"
        )


def examples(
    strategy: Strategy, bars: pl.DataFrame, count: int = COUNT, seed: int = SEED
) -> list[Example]:
    """``count`` of the strategy's entries and exits on ``bars`` (one coin, features
    attached), drawn at random: at least one of each when both exist, oldest first."""
    weights = [float(w) for w in validated_weights(strategy, bars)]
    changes = [i for i in range(1, len(weights)) if (weights[i - 1] > 0) != (weights[i] > 0)]
    entries = [i for i in changes if weights[i] > 0]
    exits = [i for i in changes if weights[i] == 0]
    rng = random.Random(seed)
    picked: list[int] = []
    for group in (entries, exits):
        if group and len(picked) < count:
            picked.append(rng.choice(group))
    rest = [i for i in changes if i not in picked]
    picked += rng.sample(rest, min(count - len(picked), len(rest)))
    explain = getattr(strategy, "waiting", None)
    times, closes = bars["open_time"].to_list(), bars["close"].to_list()
    out = []
    for i in sorted(picked):
        why = explain(bars.head(i + 1)) if callable(explain) else None
        out.append(
            Example(
                open_time=times[i],
                kind="entry" if weights[i] > 0 else "exit",
                before=weights[i - 1],
                after=weights[i],
                close=closes[i],
                rule=None if why is None else why.rule,
                conditions=[] if why is None else list(why.conditions),
            )
        )
    return out


def format_example(symbol: str, e: Example) -> str:
    agrees = {True: "agree", False: "DISAGREE: a bug, report it", None: "no tests to read"}
    lines = [
        f"{symbol}  ·  {e.kind} at the close of {e.open_time:%Y-%m-%d %H:%M} UTC "
        f"(trades at the next open)",
        f"  Weight            {e.before:.2f} -> {e.after:.2f}   close {e.close:,.6g}",
    ]
    if e.conditions:
        lines.append(f"  Tests ({e.rule} must hold)")
        for c in e.conditions:
            lines.append(
                f"    {'met    ' if c.met else 'not met'}  {c.label}: {c.subject} "
                f"{c.value:,.6g} against {c.level:,.6g}"
            )
    lines.append(f"  Tests and weight  {agrees[e.agrees]}")
    return "\n".join(lines)
