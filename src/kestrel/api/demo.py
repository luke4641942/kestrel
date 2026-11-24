"""The public demo's data: a read-only snapshot of the dashboard's API, one JSON file per
call, for a static copy of the dashboard (GitHub Pages) that needs no server.

Every file comes from the real API, called in-process (FastAPI's test client), so the demo
shows exactly what the dashboard shows. What it leaves out, and why:

- Writes (start a backtest, edit or freeze a note, chat): a static site can't run them.
- Calls that compute from typed inputs (the cost calculator) or need a market outside the
  set below: the demo says so rather than guessing.
- Long series (a 5-minute backtest's equity has a point per bar) are thinned to evenly
  spaced points for the charts; every headline number comes from the metrics, unchanged.

Before anything is written for publishing, the snapshot is checked for local paths and for
any term in an optional local list of terms never to publish (``data/demo-banned.txt``,
one per line; ``data/`` is never committed). A hit stops the export.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

MARKETS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
INTERVALS = ("1h", "4h", "1d")
MAX_BARS = 20_000
MAX_POINTS = 4_000
"""Longest series kept whole; longer ones are thinned to this many points."""
LISTS = (
    "health", "datasets", "backtests", "research", "decisions", "validation",
    "validation/history", "paper", "paper-total", "strategies", "regimes", "signal-studies",
    "event-studies", "short-studies", "cost-gate", "holdout", "fx-runs", "rules", "summary",
    "indicators", "costs",
)  # fmt: skip
BANNED_FILE = "demo-banned.txt"


class DemoError(Exception):
    """The snapshot holds something that must never be published."""


def file_for(path: str) -> str:
    """The file a call is stored in: the path, with anything but letters, digits, dot,
    dash, underscore and slash replaced by '_'. The dashboard's demo layer
    (frontend/src/demo.ts) maps a call the same way."""
    return re.sub(r"[^A-Za-z0-9._/-]", "_", path.strip("/")) + ".json"


def thin(value: Any, limit: int = MAX_POINTS) -> Any:
    """A copy with every series longer than ``limit`` thinned to ``limit`` evenly spaced
    points, the first and last kept. A series is a dict with a ``time`` list and other lists
    of the same length."""
    if isinstance(value, list):
        return [thin(v, limit) for v in value]
    if not isinstance(value, dict):
        return value
    times = value.get("time")
    if isinstance(times, list) and len(times) > limit:
        n = len(times)
        keep = sorted({round(i * (n - 1) / (limit - 1)) for i in range(limit)})
        return {
            k: [v[i] for i in keep] if isinstance(v, list) and len(v) == n else thin(v, limit)
            for k, v in value.items()
        }
    return {k: thin(v, limit) for k, v in value.items()}


@dataclass
class Snapshot:
    files: dict[str, Any] = field(default_factory=dict)
    """File name to its JSON value."""
    missing: list[str] = field(default_factory=list)
    """Calls the API refused (a market not stored, say): left out, listed."""


def collect(client: TestClient) -> Snapshot:
    snap = Snapshot()

    def get(path: str, store_as: str | None = None) -> Any:
        res = client.get(f"/api/{path}")
        if res.status_code != 200:
            snap.missing.append(path)
            return None
        body = res.json()
        snap.files[file_for(store_as or path.split("?")[0])] = body
        return body

    lists = {name: get(name) for name in LISTS}
    snap.files[file_for("health")] = {**lists["health"], "data_dir": "demo"}
    snap.files[file_for("jobs")] = []
    for run in lists["backtests"] or []:
        body = get(f"backtests/{run['id']}")
        if body is not None:
            snap.files[file_for(f"backtests/{run['id']}")] = thin(body)
    for note in lists["research"] or []:
        get(f"research/{note['slug']}")
    for account in lists["paper"] or []:
        get(f"paper/{account['name']}")
    for regime in lists["regimes"] or []:
        get(f"regimes/{regime['symbol']}")
    for strategy in lists["strategies"] or []:
        get(f"strategies/{strategy['key']}/source")
    stored = {(d["symbol"], d["interval"]) for d in lists["datasets"] or []}
    keys = [i["key"] for i in lists["indicators"] or []]
    wanted = "".join(f"&indicators={k}" for k in keys)
    for symbol in MARKETS:
        for interval in INTERVALS:
            if (symbol, interval) not in stored:
                continue
            get(f"datasets/{symbol}/{interval}/bars?limit={MAX_BARS}{wanted}")
            get(f"datasets/{symbol}/{interval}/quality")
    return snap


def check(snap: Snapshot, banned: Iterable[str], home: str) -> None:
    """Refuse a snapshot holding a local path or a banned term; the error names the files,
    never the term (so the message itself publishes nothing)."""
    terms = [t.lower() for t in banned if t.strip()]
    if home:
        terms.append(home.lower())
    hits = [
        name
        for name, value in snap.files.items()
        if any(t in json.dumps(value).lower() for t in terms)
    ]
    if hits:
        raise DemoError(f"the snapshot holds a banned term or local path in: {', '.join(hits)}")


def banned_terms(data_dir: Path) -> list[str]:
    path = data_dir / BANNED_FILE
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def commit(repo: Path) -> str | None:
    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def write(snap: Snapshot, out: Path, source_commit: str | None, now: datetime | None = None) -> int:
    """Write every file under ``out/api`` and ``out/meta.json``; returns the bytes written."""
    total = 0
    for name, value in snap.files.items():
        target = out / "api" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(value, separators=(",", ":"), allow_nan=False)
        target.write_text(text)
        total += len(text)
    meta = {
        "exported_at": (now or datetime.now(UTC)).replace(microsecond=0).isoformat(),
        "commit": source_commit,
        "markets": list(MARKETS),
        "intervals": list(INTERVALS),
        "max_points": MAX_POINTS,
        "files": len(snap.files),
        "missing": snap.missing,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return total
