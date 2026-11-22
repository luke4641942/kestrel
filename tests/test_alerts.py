"""Alerts (kestrel.execution.alerts): what each kind catches, that nothing is sent twice,
that switching alerts on never replays history, that notification text can't change the
script, and that `paper run --alerts` sends one when a run fails."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from kestrel.config import load_config
from kestrel.data.schema import Interval
from kestrel.execution import alerts, safety
from kestrel.execution.alerts import Alert, mac_notifier, review
from kestrel.execution.paper import PaperAccount, Terms, run
from tests.backtest.helpers import T0
from tests.test_paper import walk

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"
DAY = timedelta(days=1)


def account(tmp_path: Path, days: int = 100) -> tuple[PaperAccount, datetime]:
    frame = walk(days)
    terms = Terms(
        "btc", "ma-trend", "BTCUSDT", Interval.D1, {"fast": 5, "slow": 20}, Decimal(330), T0
    )
    acct = PaperAccount.open(tmp_path, terms)
    run(acct, frame, load_config(REPO_CONFIG))
    last = acct.state().last_bar
    assert last is not None
    return acct, last + 2 * DAY  # a run just after the next bar closed: on time


def test_the_first_review_only_records_then_new_events_are_sent_once(tmp_path: Path) -> None:
    acct, now = account(tmp_path)
    assert any(a.kind == "trade" for a in alerts.found(acct, now))  # it has traded
    sent: list[Alert] = []
    assert review(acct, now, sent.append) == []  # history isn't news
    assert sent == []
    safety.halt(acct, "daily loss limit")
    (halt,) = review(acct, now, sent.append)
    assert halt.kind == "halted"
    assert halt.message == "Halted: daily loss limit"
    assert review(acct, now, sent.append) == []  # not twice
    assert sent == [halt]


def test_a_failure_is_sent_once_a_day_per_error_even_on_the_first_review(tmp_path: Path) -> None:
    acct, now = account(tmp_path)
    sent: list[Alert] = []
    review(acct, now, sent.append, failure="BTCUSDT data has problems; not trading:\n2 gaps")
    review(
        acct,
        now + timedelta(hours=1),
        sent.append,
        failure="BTCUSDT data has problems; not trading:\n2 gaps",
    )
    review(acct, now + timedelta(hours=2), sent.append, failure="exchange unreachable")
    review(acct, now + DAY, sent.append, failure="exchange unreachable")
    assert [a.message for a in sent if a.kind == "failed"] == [
        "Run failed: BTCUSDT data has problems; not trading:",
        "Run failed: exchange unreachable",
        "Run failed: exchange unreachable",  # the next day: still failing, said again
    ]


def test_falling_behind_and_new_trades_are_sent(tmp_path: Path) -> None:
    acct, now = account(tmp_path, days=40)
    review(acct, now, lambda _: None)
    assert not any(a.kind == "behind" for a in alerts.found(acct, now))
    sent: list[Alert] = []
    review(acct, now + 2 * DAY, sent.append)  # two more days without a run
    (late,) = sent
    assert late.kind == "behind"
    assert "Is the Mac asleep" in late.message
    # It catches up and trades: the trades are news, the old ones aren't.
    run(acct, walk(80), load_config(REPO_CONFIG))
    caught = review(acct, acct.state().last_bar + 2 * DAY, sent.append)  # type: ignore[operator]
    assert caught
    assert {a.kind for a in caught} <= {"trade", "missed"}


def test_the_notification_text_is_passed_as_arguments_never_as_script() -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: Any) -> None:
        calls.append(args)

    nasty = 'oops" & do shell script "rm -rf ~'
    mac_notifier(fake_run)(Alert("btc", "failed", "k", nasty))
    (args,) = calls
    assert args[0] == "osascript"
    assert args[-2:] == ["Kestrel: btc", nasty]
    assert all(nasty not in a for a in args[:-1])  # only as an argument


def test_paper_run_with_alerts_sends_one_when_the_run_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import kestrel.cli as cli

    acct, _ = account(tmp_path)
    sent: list[Alert] = []
    monkeypatch.setattr(cli, "_notifier", lambda: sent.append)

    def broken(*_: Any) -> None:
        raise cli._RunFailedError("Binance said no")

    monkeypatch.setattr(cli, "_paper_run", broken)
    args = ["paper", "run", "btc", "--data-dir", str(tmp_path), "--alerts"]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 1
    assert [a.message for a in sent] == ["Run failed: Binance said no"]
    quiet = CliRunner().invoke(cli.app, args[:-1])  # without --alerts: nothing sent
    assert quiet.exit_code == 1
    assert len(sent) == 1
    assert (acct.dir / alerts.LOG).exists()
