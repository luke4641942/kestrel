"""Fail if research/decisions.toml cites a research note that git isn't tracking.

Locally the note exists, so the tests pass; on CI the checkout has only committed files and
the decision's evidence is missing. This catches that before a push."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from kestrel.decisions import load_decisions

tracked = set(
    subprocess.run(
        ["git", "ls-files", "research/strategies"], capture_output=True, text=True, check=True
    ).stdout.split()
)
missing = sorted(
    {
        f"{d.strategy} ({d.market}) cites {slug}"
        for d in load_decisions(Path("research/decisions.toml"))
        for slug in d.evidence
        if f"research/strategies/{slug}/strategy.json" not in tracked
    }
)
if missing:
    print("Decisions cite research notes that aren't committed:", *missing, sep="\n  ")
    print("Commit the notes (or the decision waits until they are).")
    sys.exit(1)
