"""Research notes: a strategy's hypothesis, written and frozen before it is tested.

Each research strategy has a record and a list of versioned notes:

    <research>/strategies/<slug>/
        strategy.json       name, status and its history
        notes/v1.json       hypothesis, rationale, rules, falsification, parameters,
                            costs, the implementation it is tested with, and when it
                            was frozen
        chat.jsonl          conversations with the research assistant

Pre-registration is enforced, not suggested. A note can only be edited while it is a
draft; it must be frozen (every section filled in) before any backtest can run against
it; and changing a frozen idea means a new version with a written reason, whose runs
count as further trials. The research folder lives in the repo so git history is
independent evidence of when each note was frozen.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from kestrel.strategy import STRATEGIES

Status = Literal["idea", "specified", "backtested", "validated", "paper", "live", "rejected"]

SECTIONS: dict[str, str] = {
    "hypothesis": "Hypothesis",
    "rationale": "Why the edge should exist",
    "rules": "Rules in plain English",
    "falsification": "What would prove it wrong",
    "parameters": "Parameters and allowed ranges",
    "costs": "Expected turnover against costs",
}
"""Every section a note must fill in before it can be frozen, with its heading."""

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ResearchError(ValueError):
    """A request that breaks the research rules (e.g. editing a frozen note)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Note:
    version: int
    created_at: str
    sections: dict[str, str]
    implementation: str | None = None
    """Key in ``kestrel.strategy.STRATEGIES`` this version is tested with."""
    frozen_at: str | None = None
    change_reason: str | None = None
    """Why this version exists (required from v2 on)."""

    @property
    def frozen(self) -> bool:
        return self.frozen_at is not None


@dataclass
class StatusChange:
    status: Status
    at: str
    reason: str


@dataclass
class ResearchStrategy:
    slug: str
    name: str
    created_at: str
    status: Status
    history: list[StatusChange] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    @property
    def latest(self) -> Note:
        return self.notes[-1]


class ResearchStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root) / "strategies"

    # ---------- reading ----------

    def strategies(self) -> list[ResearchStrategy]:
        found = [self.get(p.parent.name) for p in self.root.glob("*/strategy.json")]
        return sorted(found, key=lambda s: s.created_at)

    def get(self, slug: str) -> ResearchStrategy:
        d = self._dir(slug)
        if not (d / "strategy.json").exists():
            raise KeyError(slug)
        raw = json.loads((d / "strategy.json").read_text())
        notes = sorted(
            (Note(**json.loads(p.read_text())) for p in (d / "notes").glob("v*.json")),
            key=lambda n: n.version,
        )
        return ResearchStrategy(
            slug=raw["slug"],
            name=raw["name"],
            created_at=raw["created_at"],
            status=raw["status"],
            history=[StatusChange(**h) for h in raw["history"]],
            notes=notes,
        )

    def note(self, slug: str, version: int) -> Note:
        for n in self.get(slug).notes:
            if n.version == version:
                return n
        raise KeyError(f"{slug} v{version}")

    # ---------- writing ----------

    def create(self, name: str) -> ResearchStrategy:
        name = name.strip()
        if not name:
            raise ResearchError("give the strategy a name")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if len(slug) > 48:  # cut at a word boundary, not mid-word
            slug = slug[:49].rsplit("-", 1)[0]
        slug = slug or "strategy"
        base, n = slug, 2
        while (self._dir(slug) / "strategy.json").exists():
            slug, n = f"{base}-{n}", n + 1
        now = _now()
        strategy = ResearchStrategy(
            slug=slug,
            name=name,
            created_at=now,
            status="idea",
            history=[StatusChange("idea", now, "Created")],
            notes=[Note(1, now, dict.fromkeys(SECTIONS, ""))],
        )
        self._save(strategy)
        return strategy

    def edit(
        self,
        slug: str,
        version: int,
        sections: dict[str, str],
        implementation: str | None,
    ) -> Note:
        strategy = self.get(slug)
        note = self._latest_draft(strategy, version)
        unknown = set(sections) - set(SECTIONS)
        if unknown:
            raise ResearchError(f"unknown section(s): {', '.join(sorted(unknown))}")
        if implementation is not None and implementation not in {**STRATEGIES, **STUDIES}:
            raise ResearchError(f"unknown implementation {implementation!r}")
        note.sections.update(sections)
        note.implementation = implementation
        self._save(strategy)
        return note

    def freeze(self, slug: str, version: int) -> Note:
        strategy = self.get(slug)
        note = self._latest_draft(strategy, version)
        empty = [SECTIONS[k] for k in SECTIONS if not note.sections.get(k, "").strip()]
        if empty:
            raise ResearchError(f"fill in every section before freezing: {', '.join(empty)}")
        if note.implementation is None:
            raise ResearchError("choose the implementation this version will be tested with")
        note.frozen_at = _now()
        if strategy.status == "idea":
            self._set_status(strategy, "specified", f"v{version} frozen")
        self._save(strategy)
        return note

    def new_version(self, slug: str, reason: str) -> Note:
        strategy = self.get(slug)
        if strategy.status == "rejected":
            raise ResearchError("this strategy was rejected; start a new idea instead")
        if not strategy.latest.frozen:
            raise ResearchError(f"v{strategy.latest.version} is still a draft; edit it instead")
        if not reason.strip():
            raise ResearchError("say why the idea is changing; that reason is part of the record")
        prev = strategy.latest
        note = Note(
            version=prev.version + 1,
            created_at=_now(),
            sections=dict(prev.sections),
            implementation=prev.implementation,
            change_reason=reason.strip(),
        )
        strategy.notes.append(note)
        self._save(strategy)
        return note

    def reject(self, slug: str, reason: str) -> ResearchStrategy:
        strategy = self.get(slug)
        if not reason.strip():
            raise ResearchError("record why it was rejected; rejections are kept as evidence")
        self._set_status(strategy, "rejected", reason.strip())
        self._save(strategy)
        return strategy

    def mark_backtested(self, slug: str, version: int) -> None:
        """Called when a run is logged against a frozen version."""
        strategy = self.get(slug)
        if strategy.status == "specified":
            self._set_status(strategy, "backtested", f"first run on v{version}")
            self._save(strategy)

    def require_testable(self, slug: str) -> tuple[ResearchStrategy, Note]:
        """The latest version, if it may be backtested: frozen and not rejected."""
        try:
            strategy = self.get(slug)
        except KeyError:
            raise ResearchError(f"no research strategy {slug!r}") from None
        note = strategy.latest
        if strategy.status == "rejected":
            raise ResearchError(f"{strategy.name} was rejected")
        if not note.frozen:
            raise ResearchError(
                f"freeze {strategy.name} v{note.version} before backtesting it: the "
                "hypothesis must be written down before the results are seen"
            )
        return strategy, note

    # ---------- conversations ----------

    def append_chat(self, slug: str, messages: list[dict[str, Any]]) -> None:
        self.get(slug)
        with (self._dir(slug) / "chat.jsonl").open("a") as f:
            for m in messages:
                f.write(json.dumps(m) + "\n")

    def chat(self, slug: str) -> list[dict[str, Any]]:
        path = self._dir(slug) / "chat.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    # ---------- internals ----------

    def _dir(self, slug: str) -> Path:
        if not SLUG.match(slug):
            raise KeyError(slug)
        return self.root / slug

    def _latest_draft(self, strategy: ResearchStrategy, version: int) -> Note:
        note = strategy.latest
        if note.version != version:
            raise ResearchError(f"only the latest version (v{note.version}) can change")
        if note.frozen:
            raise ResearchError(
                f"v{version} is frozen; start a new version with a reason to change it"
            )
        if strategy.status == "rejected":
            raise ResearchError(f"{strategy.name} was rejected")
        return note

    def _set_status(self, strategy: ResearchStrategy, status: Status, reason: str) -> None:
        strategy.status = status
        strategy.history.append(StatusChange(status, _now(), reason))

    def _save(self, strategy: ResearchStrategy) -> None:
        d = self._dir(strategy.slug)
        (d / "notes").mkdir(parents=True, exist_ok=True)
        record = {
            "slug": strategy.slug,
            "name": strategy.name,
            "created_at": strategy.created_at,
            "status": strategy.status,
            "history": [asdict(h) for h in strategy.history],
        }
        _write(d / "strategy.json", record)
        for note in strategy.notes:
            _write(d / "notes" / f"v{note.version}.json", asdict(note))


STUDIES = {
    "study:volatile-direction": "kestrel volatile-study",
    "study:attention": "kestrel signal-study attention",
    "study:extreme-fear": "kestrel signal-study extreme-fear",
    "study:news-tone": "kestrel signal-study news-tone",
    "study:stablecoins": "kestrel signal-study stablecoins",
}
"""Raw-effect studies a note can name instead of a strategy: a question measured before any
trading rule exists. A backtest refuses them; each runs through its own command."""


def run_target(
    store: ResearchStore, strategy: str, research: str | None
) -> tuple[str, tuple[str, int] | None]:
    """Which implementation a backtest runs, and the trial it is logged as.

    The one place the rules for starting a backtest live, shared by the CLI and API:
    a research strategy runs only through its latest frozen note, with the
    implementation that note names; research implementations can't be run directly;
    benchmarks and engine tests run as they are and are not trials.
    """
    if research is not None:
        s, note = store.require_testable(research)
        assert note.implementation is not None  # freezing requires one
        if note.implementation in STUDIES:
            raise ResearchError(
                f"{research} is a study, not a strategy: run it with {STUDIES[note.implementation]}"
            )
        return note.implementation, (s.slug, note.version)
    if strategy not in STRATEGIES:
        raise ResearchError(f"unknown strategy {strategy!r}; one of {', '.join(STRATEGIES)}")
    if STRATEGIES[strategy].kind == "research":
        raise ResearchError("research strategies run through their research note")
    return strategy, None


def _write(path: Path, data: dict[str, Any]) -> None:
    """Write via a temp file and rename, so a crash never leaves half a note."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
