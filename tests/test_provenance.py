from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kestrel.provenance import code_version


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_inside_the_repo_records_a_commit() -> None:
    version = code_version(Path(__file__).resolve().parent)
    assert version.commit is not None
    assert len(version.commit) == 40


def test_outside_a_repo_records_nothing(tmp_path: Path) -> None:
    version = code_version(tmp_path)
    assert version.commit is None
    assert not version.dirty
