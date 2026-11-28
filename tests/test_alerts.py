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


DNS = "[Errno 8] nodename nor servname provided, or not known"


def test_a_network_failure_alerts_only_the_second_time_in_a_row(tmp_path: Path) -> None:
    acct, now = account(tmp_path)
    sent: list[Alert] = []
    review(acct, now, sent.append)  # the first review records history
    assert review(acct, now, sent.append, failure=DNS, network=True) == []  # held
    held = (acct.dir / alerts.LOG).read_text()
    assert "held: first in a row" in held  # recorded, not sent
    (again,) = review(acct, now + timedelta(hours=1), sent.append, failure=DNS, network=True)
    assert again.message == f"Run failed: {DNS} (2 runs in a row)"
    assert sent == [again]


def test_a_run_that_gets_through_resets_the_count(tmp_path: Path) -> None:
    acct, now = account(tmp_path)
    sent: list[Alert] = []
    review(acct, now, sent.append)
    review(acct, now, sent.append, failure=DNS, network=True)
    review(acct, now + timedelta(hours=1), sent.append)  # the Mac's network came back
    review(acct, now + timedelta(hours=2), sent.append, failure=DNS, network=True)
    assert sent == []  # two blips, never two in a row
    # Any other failure still alerts the first time, and breaks a network run too.
    (other,) = review(acct, now + timedelta(hours=3), sent.append, failure="bad data")
    assert other.message == "Run failed: bad data"
    assert review(acct, now + timedelta(hours=4), sent.append, failure=DNS, network=True) == []


def test_paper_run_marks_connection_errors_as_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    import kestrel.cli as cli

    account(tmp_path)
    sent: list[Alert] = []
    monkeypatch.setattr(cli, "_notifier", lambda: sent.append)

    def offline(*_: Any) -> None:
        raise httpx.ConnectError(DNS)  # the first network call of the run

    monkeypatch.setattr(cli, "spec_for", offline)
    args = ["paper", "run", "btc", "--data-dir", str(tmp_path), "--alerts"]
    assert CliRunner().invoke(cli.app, args).exit_code == 1
    assert sent == []  # the first network failure is held
    assert CliRunner().invoke(cli.app, args).exit_code == 1
    assert [a.message for a in sent] == [f"Run failed: {DNS} (2 runs in a row)"]


class FakePost:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []
        self.fail = fail

    def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> None:
        self.calls.append((url, body, headers))
        if self.fail is not None:
            raise self.fail


def test_a_phone_push_goes_to_the_topic_with_urgent_kinds_high() -> None:
    post = FakePost()
    send = alerts.ntfy_notifier("kestrel-Ab_12", post=post)
    send(Alert("tvt-btc", "halted", "k1", "Halted: drawdown limit — 25%"))
    send(Alert("tvt-btc", "trade", "k2", "Buy 0.001 BTC"))
    (url, body, urgent), (_, _, trade) = post.calls
    assert url == "https://ntfy.sh/kestrel-Ab_12"
    assert body.decode() == "Halted: drawdown limit — 25%"  # the message is the body, UTF-8
    assert urgent["Title"] == "Kestrel: tvt-btc"
    assert urgent["Priority"] == "high"
    assert trade["Priority"] == "default"
    assert set(alerts._TAGS) == set(alerts.Kind.__args__)  # type: ignore[attr-defined]


def test_a_failed_push_warns_without_the_topic_and_never_raises() -> None:
    warned: list[str] = []
    post = FakePost(fail=OSError("cannot reach https://ntfy.sh/secret-topic"))
    alerts.ntfy_notifier("secret-topic", post=post, warn=warned.append)(
        Alert("btc", "failed", "k", "Run failed")
    )
    (message,) = warned
    assert "secret-topic" not in message
    assert "OSError" in message


def test_a_bad_topic_or_server_is_refused() -> None:
    with pytest.raises(ValueError, match="KESTREL_NTFY_TOPIC"):
        alerts.ntfy_notifier("has/slash")
    with pytest.raises(ValueError, match="KESTREL_NTFY_TOPIC"):
        alerts.ntfy_notifier("x" * 65)
    with pytest.raises(ValueError, match="https"):
        alerts.ntfy_notifier("ok", server="http://ntfy.sh")


def test_the_phone_is_added_only_when_its_topic_is_set() -> None:
    mac: list[Alert] = []
    post = FakePost()
    a = Alert("btc", "trade", "k", "Buy")
    alerts.from_env({}, mac=mac.append, post=post)(a)
    assert mac == [a]
    assert post.calls == []
    alerts.from_env({"KESTREL_NTFY_TOPIC": "kestrel-x"}, mac=mac.append, post=post)(a)
    assert len(mac) == 2
    assert post.calls[0][0] == "https://ntfy.sh/kestrel-x"
    custom = {"KESTREL_NTFY_TOPIC": "t", "KESTREL_NTFY_SERVER": "https://push.example/"}
    alerts.from_env(custom, mac=mac.append, post=post)(a)
    assert post.calls[1][0] == "https://push.example/t"


def test_a_bad_topic_leaves_the_mac_notification_working() -> None:
    mac: list[Alert] = []
    warned: list[str] = []
    post = FakePost()
    send = alerts.from_env({"KESTREL_NTFY_TOPIC": "bad topic!"}, mac=mac.append, post=post,
                           warn=warned.append)  # fmt: skip
    send(Alert("btc", "trade", "k", "Buy"))
    assert len(mac) == 1
    assert post.calls == []
    assert warned
    assert "bad topic!" not in warned[0]


def test_alert_test_sends_one_alert_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kestrel import cli

    sent: list[Alert] = []
    monkeypatch.setattr(cli, "_notifier", lambda: sent.append)
    monkeypatch.delenv("KESTREL_NTFY_TOPIC", raising=False)
    monkeypatch.chdir(tmp_path)
    got = CliRunner().invoke(cli.app, ["paper", "alert-test"])
    assert got.exit_code == 0, got.output
    assert len(sent) == 1
    assert "phone is off" in got.output
    assert list(tmp_path.iterdir()) == []
