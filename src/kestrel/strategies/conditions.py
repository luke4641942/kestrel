"""What a strategy is waiting for: its entry tests at the latest close, for the Trading page
("why is it in cash?").

Each test compares a close with a level (an average, or the close some days before). Besides
whether it is met, it says how far that close would have to move *by the next close* to flip
the test, exactly: the next average includes the new close, and the next "N days ago" is one
bar later. Strategies build their tests from these helpers, and a test checks that the tests
agree with the strategy's own decision (tests/test_conditions.py), so this view can't drift
from the rule it describes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Condition:
    label: str
    """What must hold, e.g. "BTC above its 50-day average + 3%"."""
    subject: str
    """Whose close it tests (a symbol)."""
    value: float
    """That close now."""
    level: float
    """The level it is tested against now."""
    met: bool
    move: float
    """The change in the subject's close by the next close that flips the test (a fraction:
    positive when it must rise to pass; negative when met, how far it can fall)."""


@dataclass(frozen=True)
class Waiting:
    bar: datetime
    """Open time of the latest closed bar the tests read."""
    rule: Literal["all", "any"]
    """In the market when all tests hold, or (sized by how many) when any does."""
    conditions: list[Condition]
    decides: str = "every close"
    """When it decides: "every close", or "Sunday closes"."""
    would_hold: list[str] = field(default_factory=list)
    """A rotation: the coins it would hold if its tests were met now."""


def above_average(
    label: str, subject: str, closes: Sequence[float], days: int, margin: float = 0.0
) -> Condition | None:
    """``close > (1 + margin) x its days-bar average`` (the average includes today). None
    with too little history."""
    if len(closes) < days:
        return None
    k = 1 + margin
    now = closes[-1]
    level = k * sum(closes[-days:]) / days
    # Next close c joins the average and the oldest leaves: c > k (S + c) / days, with S the
    # last days - 1 closes, so c > k S / (days - k).
    rest = sum(closes[len(closes) - days + 1 :])
    needed = k * rest / (days - k)
    return Condition(label, subject, now, level, now > level, needed / now - 1)


def above_past(label: str, subject: str, closes: Sequence[float], bars: int) -> Condition | None:
    """``close > the close bars ago``. None with too little history."""
    if len(closes) <= bars:
        return None
    now, then = closes[-1], closes[-1 - bars]
    nxt = closes[-bars]  # the close bars before the next one
    return Condition(label, subject, now, then, now > then, nxt / now - 1)
