"""Atomic file replacement that concurrent writers can share.

Every store writes a temporary file and renames it over the target, so a reader sees the
old file or the new one, never half of one. The temporary name must be unique to each
write: the paper accounts run as separate launchd jobs at the same minute, and three of
them refresh ETHUSDT. With one fixed name (``month=2026-11.parquet.tmp``) the first job's
rename took the second job's file away, and the second crashed (FileNotFoundError,
2026-11-21 to 23). Both jobs write the same merged bars, so the last rename winning is
correct; the crash was not.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from pathlib import Path


def temp_path(target: Path) -> Path:
    """A hidden sibling of ``target`` no other write will use."""
    return target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")


def write_atomically(target: Path, write: Callable[[Path], object]) -> Path:
    """Call ``write`` on a unique temporary path, then rename it over ``target``. The
    temporary file is removed if writing fails."""
    tmp = temp_path(target)
    try:
        write(tmp)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target
