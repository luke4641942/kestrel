"""The paper status export (kestrel.execution.status): the snapshot, and its publishing to
its own branch through git plumbing. Publishing runs real git against a local bare
repository standing in for GitHub, so it stays offline."""

from __future__ import annotations

import json
import plistlib
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.config import load_config
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.execution import safety
from kestrel.execution import status as export
from kestrel.execution.paper import PaperAccount, Terms, run
from tests.backtest.helpers import T0
from tests.test_paper import walk

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"
NOW = datetime(2026, 11, 23, 12, tzinfo=UTC)


def accounts(data: Path) -> None:
    """Two accounts on 200 stored bars, run to different bars (decay checks not yet due)."""
    frame = walk(200)
    ParquetStore(data).write("BTCUSDT", Interval.D1, frame)
    terms = Terms(
        "btc", "ma-trend", "BTCUSDT", Interval.D1, {"fast": 5, "slow": 20}, Decimal(330), T0
    )
    run(PaperAccount.open(data, terms), frame.head(80), load_config(REPO_CONFIG))
    tvt = Terms("tvt", "trend-vol-target", "BTCUSDT", Interval.D1, {}, Decimal(330), T0)
    later = PaperAccount.open(data, tvt)
    run(later, frame.head(60), load_config(REPO_CONFIG))
    safety.halt(later, "testing")


def test_the_snapshot_says_where_every_account_stands(tmp_path: Path) -> None:
    accounts(tmp_path)
    snap = export.snapshot(tmp_path, load_config(REPO_CONFIG), NOW)
    btc, tvt = snap["accounts"]
    assert snap["written_at"] == "2026-11-23T12:00:00+00:00"
    assert snap["as_of"] == btc["last_bar"] > tvt["last_bar"]
    assert btc["days_handled"] == 79  # the last bar's close decides tomorrow's open
    assert btc["profit_usdt"] == pytest.approx(btc["equity_usdt"] - 330, abs=0.01)
    assert btc["model"]["decision_mismatches"] == 0  # the sim venue is the model
    assert btc["model"]["equity_usdt"] == btc["equity_usdt"]
    assert btc["trades"] >= 1
    assert btc["waiting"] is None  # ma-trend doesn't describe its tests
    assert btc["decay"] == {
        "status": "too early", "days": 78, "due_at_days": 90, "sharpe": None, "floor": None,
    }  # fmt: skip
    if btc["holding"]:
        assert btc["exposure_pct"] > 0
    assert [c["label"] for c in tvt["waiting"]["conditions"]] == [
        "BTC up over 20 days", "BTC up over 60 days", "BTC up over 120 days",
    ]  # fmt: skip
    total = snap["total"]
    assert total["accounts"] == 2
    assert total["equity_usdt"] == pytest.approx(btc["equity_usdt"] + tvt["equity_usdt"])
    assert (total["halted"], total["behind"]) == (["tvt"], ["tvt"])
    json.loads(export.render(snap))  # it is valid JSON


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working repo with one commit on main and a bare 'origin', with no global git
    settings (signing, hooks) leaking in."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    remote, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "a.txt").write_text("work in progress\n")
    git(work, "add", "a.txt")
    git(work, "commit", "-q", "-m", "first")
    git(work, "remote", "add", "origin", str(remote))
    (work / "a.txt").write_text("uncommitted change\n")  # must survive every publish
    return work


def doc(equity: float, written: str = "2026-11-23T12:00:00+00:00") -> str:
    return export.render({"schema": 1, "written_at": written, "as_of": "2026-11-22",
                          "total": {"equity_usdt": equity}})  # fmt: skip


def test_publishing_commits_only_changes_to_its_own_branch(repo: Path) -> None:
    run_git = export.git_in(repo)
    head, status = git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain")

    assert export.publish(run_git, doc(100)).endswith("pushed paper-status to origin")
    remote = repo.parent / "origin.git"
    assert json.loads(git(remote, "show", f"paper-status:{export.FILE}"))["total"] == {
        "equity_usdt": 100
    }
    assert git(remote, "ls-tree", "--name-only", "paper-status") == export.FILE  # one file

    # Only the time it was written differs: no commit, and nothing to push.
    said = export.publish(run_git, doc(100, "2026-11-23T13:00:00+00:00"))
    assert said == "unchanged; origin already has it"

    said = export.publish(run_git, doc(101))
    assert said.startswith("committed")
    assert said.endswith("pushed paper-status to origin")
    assert git(remote, "rev-list", "--count", "paper-status") == "2"  # history kept
    assert git(remote, "log", "-1", "--format=%s", "paper-status") == (
        "Paper status as of 2026-11-22"
    )

    # main, the index and the working tree are untouched; nothing reached origin's main.
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "branch", "--show-current") == "main"
    assert git(repo, "status", "--porcelain") == status
    assert git(remote, "branch", "--list", "main") == ""


def test_a_failed_push_is_reported_and_retried_next_time(repo: Path) -> None:
    run_git = export.git_in(repo)
    remote = repo.parent / "origin.git"
    remote.rename(repo.parent / "away.git")
    with pytest.raises(export.PublishError, match="git push failed"):
        export.publish(run_git, doc(100))
    assert export.publish(run_git, doc(100), push=False) == "unchanged"  # committed locally
    (repo.parent / "away.git").rename(remote)
    assert export.publish(run_git, doc(100)) == "unchanged; pushed paper-status to origin"


def test_the_commands(tmp_path: Path, repo: Path) -> None:
    data = tmp_path / "data"
    accounts(data)
    common = ["--data-dir", str(data), "--config", str(REPO_CONFIG)]
    shown = CliRunner().invoke(app, ["paper", "export", *common])
    assert shown.exit_code == 0, shown.output
    assert [a["name"] for a in json.loads(shown.output)["accounts"]] == ["btc", "tvt"]

    pushed = CliRunner().invoke(app, ["paper", "export", "--push", "--repo", str(repo), *common])
    assert pushed.exit_code == 0, pushed.output
    assert "pushed paper-status to origin" in pushed.output
    failed = CliRunner().invoke(
        app, ["paper", "export", "--push", "--repo", str(tmp_path / "nowhere"), *common]
    )
    assert failed.exit_code == 1

    agents = tmp_path / "agents"
    wrote = CliRunner().invoke(
        app, ["paper", "schedule-export", "--agents-dir", str(agents), "--repo", str(repo),
              *common],
    )  # fmt: skip
    assert wrote.exit_code == 0, wrote.output
    job = plistlib.loads((agents / "com.kestrel.paper-export.plist").read_bytes())
    assert job["StartCalendarInterval"] == [{"Minute": 40}]
    assert "paper export --push" in job["ProgramArguments"][-1]
    assert str(repo.resolve()) in job["ProgramArguments"][-1]
    gone = CliRunner().invoke(
        app, ["paper", "schedule-export", "--remove", "--agents-dir", str(agents)]
    )
    assert gone.exit_code == 0
    assert not (agents / "com.kestrel.paper-export.plist").exists()
