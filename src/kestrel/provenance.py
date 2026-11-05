"""Where a result came from: the code version, so any run can be traced and reproduced."""

from __future__ import annotations

import functools
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodeVersion:
    commit: str | None
    """Git commit hash, or None outside a git checkout."""
    dirty: bool
    """True if there were uncommitted changes, so the commit alone can't reproduce it."""


@functools.cache
def code_version(repo: Path | None = None) -> CodeVersion:
    """The git commit and dirty flag, looked up once per process.

    Cached because a process runs the code it loaded at start, so the first answer is the
    accurate one; asking git again after a file is edited would describe code that isn't
    running. (`kestrel serve --reload` restarts the process, so it looks up afresh.) The
    lookup is a subprocess (~30 ms), which also dominated the cost of a backtest."""
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
