"""The research rules: thresholds stated once, the findings lessons parsed, and the endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.validation import rules

REPO = Path(__file__).resolve().parents[1]

FINDINGS = """# Findings

## What the evidence says

1. **Trend limits losses.** It cut drawdowns
   by half, but holding less does too.
2. **Patterns failed.** None beat its control.

## Every hypothesis

1. **Not a lesson.** Outside the section.
"""


def test_the_scorecard_states_its_thresholds_from_the_constants() -> None:
    rules_text = " ".join(t.rule for t in rules.SCORECARD)
    assert len(rules.SCORECARD) == 8
    assert "95%" in rules_text
    assert "50%" in rules_text
    assert "30 trades" in rules_text
    assert "2x" in rules_text
    assert str(rules.MAX_COINS) in rules.ACCOUNT


def test_lessons_join_wrapped_lines_and_stop_at_the_next_section() -> None:
    found = rules.lessons(FINDINGS)
    assert [x.title for x in found] == ["Trend limits losses.", "Patterns failed."]
    assert found[0].text == "It cut drawdowns by half, but holding less does too."
    with pytest.raises(ValueError, match="What the evidence says"):
        rules.lessons("# Findings\n")


def test_the_repos_findings_parse() -> None:
    assert len(rules.lessons((REPO / "research" / "findings.md").read_text())) >= 5


def test_the_endpoint(tmp_path: Path) -> None:
    research = tmp_path / "research"
    research.mkdir()
    client = TestClient(create_app(tmp_path, static_dir=None, research_dir=research))
    body = client.get("/api/rules").json()
    assert body["reviewed"] == "2026-11-14"
    assert [t["name"] for t in body["scorecard"]][:2] == ["Edge", "Luck"]
    assert body["lessons"] == []  # no findings file yet
    (research / "findings.md").write_text(FINDINGS)
    assert client.get("/api/rules").json()["lessons"][1]["title"] == "Patterns failed."
    (research / "findings.md").write_text("# Findings\n")
    assert client.get("/api/rules").status_code == 503
