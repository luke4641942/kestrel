"""Repository hygiene: guards against source silently missing from version control.

An unanchored ``data/`` in .gitignore once matched src/kestrel/data/ and
tests/data/, so the whole data engine was never committed while every local
check still passed. This test catches that class of mistake on the dev machine,
where the files exist but git would not ship them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def in_git_repo() -> None:
    if shutil.which("git") is None or _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not running inside a git checkout")


@pytest.mark.usefixtures("in_git_repo")
def test_no_source_or_test_file_is_gitignored() -> None:
    files = [
        str(p.relative_to(ROOT))
        for top in ("src", "tests")
        for p in (ROOT / top).rglob("*.py")
        if "__pycache__" not in p.parts
    ]
    result = _git("check-ignore", "--no-index", *files)
    ignored = result.stdout.split()
    assert ignored == [], f"gitignored source files (fix .gitignore): {ignored}"


@pytest.mark.usefixtures("in_git_repo")
def test_market_data_dir_is_still_ignored() -> None:
    assert _git("check-ignore", "--no-index", "data/klines/x.parquet").returncode == 0
