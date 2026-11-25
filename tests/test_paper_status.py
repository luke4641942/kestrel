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
    # Holding BTC from the first close handled (bar 0) to the last marked (bar 78), costs paid.
    closes = walk(200)["close"]
    costs = load_config(REPO_CONFIG).costs
    entry = (1 + float(costs.half_spread_bps) / 1e4) * (1 + float(costs.taker_fee_bps) / 1e4)
    held = (closes[78] / closes[0] / entry - 1) * 100
    assert btc["hold_pct"] == pytest.approx(held, abs=0.01)
    assert btc["vs_hold_pct"] == pytest.approx(btc["profit_pct"] - btc["hold_pct"], abs=0.02)
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
    """A working repo with one commit on main, a bare 'origin' (the public repository) and
    a bare 'status' (the private one), with no global git settings leaking in."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    work = tmp_path / "work"
    for bare in ("origin.git", "status.git"):
        subprocess.run(["git", "init", "--bare", "-q", str(tmp_path / bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "a.txt").write_text("work in progress\n")
    git(work, "add", "a.txt")
    git(work, "commit", "-q", "-m", "first")
    git(work, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(work, "remote", "add", "status", str(tmp_path / "status.git"))
    (work / "a.txt").write_text("uncommitted change\n")  # must survive every publish
    return work


def doc(equity: float, written: str = "2026-11-23T12:00:00+00:00") -> str:
    return export.render({"schema": 1, "written_at": written, "as_of": "2026-11-22",
                          "total": {"equity_usdt": equity}})  # fmt: skip


def files(equity: float, todo: str = "# To-do\n", written: str = "2026-11-23T12:00:00+00:00"):  # type: ignore[no-untyped-def]
    return {export.FILE: doc(equity, written), "TODO.md": todo}


def test_publishing_commits_only_changes_to_the_private_repository(repo: Path) -> None:
    run_git = export.git_in(repo)
    head, status = git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain")
    private, public = repo.parent / "status.git", repo.parent / "origin.git"

    assert export.publish(run_git, files(100)).endswith("pushed to status")
    assert git(private, "ls-tree", "--name-only", "main").split() == ["TODO.md", export.FILE]
    assert json.loads(git(private, "show", f"main:{export.FILE}"))["total"] == {"equity_usdt": 100}

    # Only the time it was written differs: no commit, and nothing to push.
    said = export.publish(run_git, files(100, written="2026-11-23T13:00:00+00:00"))
    assert said == "unchanged; status already has it"

    said = export.publish(run_git, files(100, todo="# To-do\n- new\n"))
    assert said.startswith("committed")
    assert git(private, "rev-list", "--count", "main") == "2"  # history kept
    assert git(private, "log", "-1", "--format=%s", "main") == "Status as of 2026-11-22"

    # This repository: main, the index and the working tree untouched; no branch made;
    # nothing ever reaches the public remote, even when every branch is pushed.
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == status
    assert git(repo, "branch", "--format=%(refname:short)") == "main"
    git(repo, "push", "-q", "--all", "origin")
    assert git(public, "for-each-ref", "--format=%(refname)") == "refs/heads/main"
    assert export.FILE not in git(public, "ls-tree", "--name-only", "main")


def test_a_failed_push_is_reported_and_retried_next_time(repo: Path) -> None:
    run_git = export.git_in(repo)
    private = repo.parent / "status.git"
    private.rename(repo.parent / "away.git")
    with pytest.raises(export.PublishError, match="git push failed"):
        export.publish(run_git, files(100))
    assert export.publish(run_git, files(100), push=False) == "unchanged"  # committed locally
    (repo.parent / "away.git").rename(private)
    assert export.publish(run_git, files(100)) == "unchanged; pushed to status"
    with pytest.raises(export.PublishError, match="nothing"):
        export.publish(run_git, {})


def test_the_commands(tmp_path: Path, repo: Path) -> None:
    data = tmp_path / "data"
    accounts(data)
    common = ["--data-dir", str(data), "--config", str(REPO_CONFIG)]
    shown = CliRunner().invoke(app, ["paper", "export", *common])
    assert shown.exit_code == 0, shown.output
    assert [a["name"] for a in json.loads(shown.output)["accounts"]] == ["btc", "tvt"]

    (repo / "TODO.md").write_text("# To-do\n- private\n")
    pushed = CliRunner().invoke(app, ["paper", "export", "--push", "--repo", str(repo), *common])
    assert pushed.exit_code == 0, pushed.output
    assert "pushed to status" in pushed.output
    assert git(repo.parent / "status.git", "show", "main:TODO.md") == "# To-do\n- private"
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
