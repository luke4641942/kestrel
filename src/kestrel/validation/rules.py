"""The research rules agreed in the 2026-11-14 review: what a pass means, the one scorecard
every strategy is judged on, and the bar a new idea must clear before it costs a trial.

This module is the single source for the dashboard's Rules page and for the grading
(kestrel.validation.scorecard); CLAUDE.md describes the same rules in prose. The
thresholds are constants so the grader and the page can't disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

REVIEWED = date(2026, 11, 14)

DEFLATED_SHARPE_MIN = 0.95
MAX_DRAWDOWN = 0.50
MIN_TRADES = 30
BREAK_EVEN_MULTIPLE = 2.0
START_WINDOW_SHARE = 0.5
MAX_COINS = 3

PASS = (
    "A strategy passes only if it beats holding the same coin at the same average exposure "
    "on Sharpe, after costs, and its deflated Sharpe clears the bar. Lower drawdown alone is "
    "not an edge: holding less of the coin gives it for free. Drawdown is a limit, not a goal."
)

RISK_MANAGED = (
    "A strategy that hasn't passed but is kept because it smooths the ride. It is labelled "
    "'not a proven edge' and never counts as a pass. Trend-vol-target on BTC goes to paper "
    "trading on this basis: it beat holding BTC at the same exposure, and passed the holdout, "
    "but didn't clear the luck bar."
)


@dataclass(frozen=True)
class Test:
    name: str
    rule: str
    why: str


SCORECARD: tuple[Test, ...] = (
    Test(
        "Edge",
        "Sharpe beats holding the same coin at the same average exposure, after costs.",
        "Being out of the market some of the time is free; only a better return per unit of "
        "risk is skill.",
    ),
    Test(
        "Luck",
        f"Deflated Sharpe at least {DEFLATED_SHARPE_MIN:.0%}, counting every trial.",
        "The best of many worthless strategies looks good by chance; this corrects for how "
        "many were tried.",
    ),
    Test(
        "Out of sample",
        "Walk-forward: most test windows still beat exposure-matched holding.",
        "Settings chosen on the past must keep working on months they never saw.",
    ),
    Test(
        "Start dates",
        f"Beats exposure-matched holding in at least {START_WINDOW_SHARE:.0%} of 2-year "
        "start windows.",
        "A result that depends on starting in one lucky year isn't one you could have had.",
    ),
    Test(
        "Costs",
        f"Break-even cost at least {BREAK_EVEN_MULTIPLE:g}x the actual cost.",
        "Real fills will be worse than modelled; the edge needs room to absorb that.",
    ),
    Test(
        "Enough trades",
        f"At least {MIN_TRADES} trades.",
        "Fewer trades than this can't tell skill from a few good bets.",
    ),
    Test(
        "Drawdown limit",
        f"Worst drawdown no deeper than {MAX_DRAWDOWN:.0%}.",
        "A strategy you would abandon halfway down earns nothing.",
    ),
    Test(
        "Robustness",
        "Neighbouring settings also pass the edge test.",
        "A plateau of good settings is a pattern; a single peak is noise that was fitted.",
    ),
)

ENTRY_BAR: tuple[str, ...] = (
    "Who pays you and why: an economic reason, not a chart pattern.",
    "Outside evidence: a published or out-of-sample result from somewhere else.",
    "Not a variant of a rejected family (pullbacks, chart patterns, reversals).",
    "How many runs it will use, declared before any run.",
)

ACCOUNT = (
    f"Research at the real account size (about A$500, 330 USDT). Binance refuses orders under "
    f"5 USDT, so a strategy holds at most {MAX_COINS} coins at once."
)


@dataclass(frozen=True)
class Lesson:
    title: str
    text: str


_HEADING = "## What the evidence says"
_ITEM = re.compile(r"^\d+\.\s+\*\*(.+?)\*\*\s*(.*)$")


def lessons(findings: str) -> list[Lesson]:
    """The numbered lessons under 'What the evidence says' in research/findings.md, each a
    bold title and its text. Raises if the section is missing, so a renamed heading fails
    loudly instead of showing an empty page."""
    if _HEADING not in findings:
        raise ValueError(f"findings has no '{_HEADING}' section")
    section = findings.split(_HEADING, 1)[1].split("\n## ", 1)[0]
    out: list[Lesson] = []
    for line in section.splitlines():
        item = _ITEM.match(line.strip())
        if item:
            out.append(Lesson(item[1], item[2]))
        elif out and line.startswith("   ") and line.strip():
            last = out[-1]
            out[-1] = Lesson(last.title, f"{last.text} {line.strip()}".strip())
    return out
