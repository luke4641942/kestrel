"""Where a result came from: the code version, so any run can be traced and reproduced."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodeVersion:
    commit: str | None
    """Git commit hash, or None outside a git checkout."""
    dirty: bool
    """True if there were uncommitted changes, so the commit alone can't reproduce it."""


def code_version(repo: Path | None = None) -> CodeVersion:
    cwd = repo or Path(__file__).resolve().parent

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=5
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip()

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=no")
    return CodeVersion(commit=commit, dirty=bool(status))
