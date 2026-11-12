"""Every validation study, kept so its looks count as trials.

A robustness check or a walk-forward tries many parameter settings on data that has been
seen. None of them is saved as a run, but each is still a look, and the deflated Sharpe
(kestrel.validation.deflated) must count it. So every study is saved here with the
Sharpe of each setting it tried:

    <data>/validation/<study id>.json

Studies are written atomically and never edited, like runs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class Look:
    """One parameter setting a study tried, and the annual Sharpe it showed."""

    params: dict[str, int]
    sharpe: float | None


@dataclass(frozen=True)
class Study:
    kind: Literal["robustness", "walk-forward"]
    strategy: str
    symbol: str
    interval: str
    start: str
    end: str
    looks: list[Look]
    verdict: str
    detail: dict[str, Any] = field(default_factory=dict)
    """What the report showed: per-setting results, folds, ratios."""


class ValidationLog:
    def __init__(self, data_dir: Path | str) -> None:
        self.root = Path(data_dir) / "validation"

    def save(self, study: Study) -> str:
        created = datetime.now(UTC)
        study_id = f"{created:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.root.mkdir(parents=True, exist_ok=True)
        doc = {"id": study_id, "created_at": created.isoformat(), **asdict(study)}
        tmp = self.root / f".{study_id}.tmp"
        tmp.write_text(json.dumps(doc, indent=2, default=str))
        tmp.replace(self.root / f"{study_id}.json")
        return study_id

    def studies(self) -> list[dict[str, Any]]:
        """Saved studies, newest first."""
        found = [json.loads(p.read_text()) for p in self.root.glob("*.json")]
        return sorted(found, key=lambda s: s["created_at"], reverse=True)
