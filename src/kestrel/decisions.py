"""What to do with each tested strategy: pursue, watch or drop.

The research notes, runs and validation studies are evidence; this is the judgement made
from all of it, one line per strategy and market, kept in research/decisions.toml under
version control. Like a note's rejection, a decision carries its reason and date, and
changing one means writing a new reason. The dashboard shows this list first, so the
answer to "what should I work on?" doesn't depend on reading the metrics.

Loaded strictly, like kestrel.toml: unknown keys, unknown strategies, empty reasons and
unrecognised decisions are errors, never silently skipped.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

from kestrel.strategy import STRATEGIES

Choice = Literal["pursue", "watch", "drop"]
CHOICES: tuple[Choice, ...] = ("pursue", "watch", "drop")
"""In the order they are shown."""


class DecisionError(ValueError):
    """research/decisions.toml is missing something or says something it can't."""


@dataclass(frozen=True)
class Decision:
    strategy: str
    """Key in kestrel.strategy.STRATEGIES."""
    name: str
    market: str
    decision: Choice
    decided: date
    reason: str
    evidence: list[str] = field(default_factory=list)
    """Research note slugs, run ids or study ids the decision rests on."""


REQUIRED = {"strategy", "name", "market", "decision", "decided", "reason"}


def load_decisions(path: Path | str) -> list[Decision]:
    """Every decision, pursue first, then watch, then drop. A missing file is an empty
    list: nothing has been decided yet."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        document = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise DecisionError(f"{path} is not valid TOML: {e}") from None
    unknown = set(document) - {"decision"}
    if unknown:
        raise DecisionError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    entries = document.get("decision", [])
    if not isinstance(entries, list):
        raise DecisionError(f"{path}: decisions are [[decision]] tables")
    out = [_parse(entry, i) for i, entry in enumerate(entries, start=1)]
    seen: set[tuple[str, str]] = set()
    for d in out:
        if (d.strategy, d.market) in seen:
            raise DecisionError(f"two decisions for {d.strategy} on {d.market}; keep one")
        seen.add((d.strategy, d.market))
    return sorted(out, key=lambda d: CHOICES.index(d.decision))


def _parse(entry: Any, n: int) -> Decision:
    where = f"decision {n}"
    if not isinstance(entry, dict):
        raise DecisionError(f"{where} must be a table")
    missing = REQUIRED - entry.keys()
    unknown = entry.keys() - REQUIRED - {"evidence"}
    if missing:
        raise DecisionError(f"{where}: missing {', '.join(sorted(missing))}")
    if unknown:
        raise DecisionError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    if entry["strategy"] not in STRATEGIES:
        raise DecisionError(f"{where}: no strategy {entry['strategy']!r}")
    if entry["decision"] not in CHOICES:
        raise DecisionError(f"{where}: decision must be one of {', '.join(CHOICES)}")
    for key in ("name", "market", "reason"):
        if not isinstance(entry[key], str) or not entry[key].strip():
            raise DecisionError(f"{where}: {key} must be written out")
    decided = entry["decided"]
    if not isinstance(decided, date) or hasattr(decided, "hour"):
        raise DecisionError(f"{where}: decided must be a date like 2026-11-12")
    evidence = entry.get("evidence", [])
    if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
        raise DecisionError(f"{where}: evidence must be a list of ids")
    return Decision(
        strategy=entry["strategy"],
        name=entry["name"].strip(),
        market=entry["market"].strip(),
        decision=cast(Choice, entry["decision"]),
        decided=decided,
        reason=entry["reason"].strip(),
        evidence=evidence,
    )
