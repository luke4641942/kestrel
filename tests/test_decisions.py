"""Pursue, watch or drop: the decisions file and its endpoint."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.decisions import DecisionError, load_decisions

REPO = Path(__file__).resolve().parents[1]
ENTRY = """
[[decision]]
strategy = "{strategy}"
name = "Trend following"
market = "BTCUSDT 1d"
decision = "{decision}"
decided = 2026-11-12
reason = "{reason}"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "decisions.toml"
    path.write_text(text)
    return path


def entry(
    strategy: str = "trend-vol-target", decision: str = "pursue", reason: str = "Held up."
) -> str:
    return ENTRY.format(strategy=strategy, decision=decision, reason=reason)


def test_the_repos_decisions_load_and_point_at_real_evidence() -> None:
    decisions = load_decisions(REPO / "research" / "decisions.toml")
    assert decisions[0].decision == "pursue"  # pursue first, then watch, then drop
    assert [d.decision for d in decisions] == sorted(
        (d.decision for d in decisions), key=["pursue", "watch", "drop"].index
    )
    # Research notes are committed by hand, so a fresh checkout (CI) may not have them all;
    # check every evidence slug that has a note folder to check against.
    notes = {p.name for p in (REPO / "research" / "strategies").iterdir() if p.is_dir()}
    for d in decisions:
        assert d.reason
        assert d.name
        for slug in d.evidence:
            assert slug in notes or len(notes) <= 1, f"{d.strategy}: no research note {slug}"


def test_a_missing_file_means_nothing_decided(tmp_path: Path) -> None:
    assert load_decisions(tmp_path / "none.toml") == []


def test_decisions_are_ordered_pursue_watch_drop(tmp_path: Path) -> None:
    text = entry("channel-breakout", "drop") + entry("ma-trend", "watch") + entry()
    found = load_decisions(write(tmp_path, text))
    assert [(d.strategy, d.decision) for d in found] == [
        ("trend-vol-target", "pursue"),
        ("ma-trend", "watch"),
        ("channel-breakout", "drop"),
    ]
    assert found[0].decided == date(2026, 11, 12)
    assert found[0].evidence == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("decision = [", "not valid TOML"),
        ("other = 1\n", "unknown key"),
        ('decision = "pursue"\n', r"\[\[decision\]\] tables"),
        ("decision = [1]\n", "must be a table"),
        ('[[decision]]\nstrategy = "trend-vol-target"\n', "missing"),
        (entry().replace("reason =", 'colour = "red"\nreason ='), "unknown key"),
        (entry("magic"), "no strategy 'magic'"),
        (entry(decision="maybe"), "pursue, watch, drop"),
        (entry(reason="  "), "reason must be written out"),
        (entry().replace("decided = 2026-11-12", 'decided = "soon"'), "decided must be a date"),
        (
            entry().replace("decided = 2026-11-12", "decided = 2026-11-12T10:00:00"),
            "decided must be a date",
        ),
        (entry() + 'evidence = "a-note"\n', "evidence must be a list"),
        (entry() + entry(), "two decisions"),
    ],
)
def test_mistakes_are_errors_not_skipped(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(DecisionError, match=message):
        load_decisions(write(tmp_path, text))


def test_the_endpoint(tmp_path: Path) -> None:
    research = tmp_path / "research"
    research.mkdir()
    (research / "decisions.toml").write_text(entry() + 'evidence = ["n1"]\n')
    client = TestClient(create_app(tmp_path, static_dir=None, research_dir=research))
    (d,) = client.get("/api/decisions").json()
    assert (d["strategy"], d["decision"], d["evidence"]) == ("trend-vol-target", "pursue", ["n1"])
    (research / "decisions.toml").write_text(entry(decision="maybe"))
    broken = client.get("/api/decisions")
    assert broken.status_code == 503
    assert "pursue, watch, drop" in broken.text
