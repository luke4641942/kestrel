"""kestrel paper open / run / status, offline: a fake Binance endpoint and a frozen clock."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

import kestrel.cli as cli
from kestrel.data import live
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.execution import safety
from kestrel.execution.paper import PaperAccount
from tests.backtest.helpers import T0, bars
from tests.data.test_live import FakeBinance

DAY = timedelta(days=1)
REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")
NOW = T0 + 102 * DAY + timedelta(hours=1)  # day 101 just closed; day 102 is forming


class Frozen(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def,override]
        return NOW


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeBinance:
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.D1, bars([100.0 + i for i in range(100)], step=DAY))
    fake = FakeBinance(days=103)
    clients = iter([fake.client() for _ in range(10)])  # made before httpx.Client is patched
    monkeypatch.setattr(cli.httpx, "Client", lambda **_: next(clients))
    monkeypatch.setattr(cli, "datetime", Frozen)
    monkeypatch.setattr(live, "datetime", Frozen)
    return fake


def invoke(tmp_path: Path, *args: str):  # type: ignore[no-untyped-def]
    common = ["--data-dir", str(tmp_path)]
    return CliRunner().invoke(cli.app, ["paper", *args, *common])


def test_open_run_and_status(tmp_path: Path, setup: FakeBinance) -> None:
    opened = invoke(
        tmp_path, "open", "btc", "--symbol", "btcusdt", "--strategy", "ma-trend", "--cash", "330"
    )
    assert opened.exit_code == 0, opened.output
    assert "first decision on the 2024-04-11 00:00 UTC bar" in opened.output  # day 101
    ran = invoke(tmp_path, "run", "btc", "--config", REPO_CONFIG)
    assert ran.exit_code == 0, ran.output
    assert ran.output.startswith("2024-04-11: equity 330.00, target 100%: buy")
    account = PaperAccount(tmp_path, "btc")
    decision = next(e for e in account.ledger.events() if e["type"] == "decision")
    assert decision["price"] == "100.0"  # the forming bar's open, from the live API
    assert ParquetStore(tmp_path).read("BTCUSDT", Interval.D1).height == 102  # updated first
    again = invoke(tmp_path, "run", "btc", "--config", REPO_CONFIG)
    assert "nothing new" in again.output
    status = invoke(tmp_path, "status", "btc")
    assert "btc: ma-trend on BTCUSDT 1d" in status.output
    assert "1 fills; 1 days handled" in status.output


def test_research_strategies_need_their_note(tmp_path: Path, setup: FakeBinance) -> None:
    refused = invoke(tmp_path, "open", "t", "--symbol", "BTCUSDT", "--strategy", "trend-vol-target")
    assert refused.exit_code == 1
    assert "research note" in refused.output
    assert invoke(tmp_path, "status", "t").exit_code == 1  # nothing was opened


def test_it_refuses_to_trade_on_bad_data(tmp_path: Path, setup: FakeBinance) -> None:
    setup.rows[101][2] = "50.0"  # day 101's high below its close: an OHLC violation
    invoke(tmp_path, "open", "btc", "--symbol", "BTCUSDT", "--strategy", "ma-trend")
    ran = invoke(tmp_path, "run", "btc", "--config", REPO_CONFIG)
    assert ran.exit_code == 1
    assert "data has problems; not trading" in ran.output
    assert not [e for e in PaperAccount(tmp_path, "btc").ledger.events() if e["type"] == "decision"]


def test_kill_halts_and_resume_lifts_it(tmp_path: Path, setup: FakeBinance) -> None:
    invoke(tmp_path, "open", "btc", "--symbol", "BTCUSDT", "--strategy", "ma-trend")
    invoke(tmp_path, "run", "btc", "--config", REPO_CONFIG)
    killed = CliRunner().invoke(
        cli.app,
        ["kill", "btc", "--reason", "drill", "--data-dir", str(tmp_path), "--config", REPO_CONFIG],
    )
    assert killed.exit_code == 0, killed.output
    assert "KILLED btc: sold" in killed.output
    account = PaperAccount(tmp_path, "btc")
    (kill,) = [e for e in account.ledger.events() if e["type"] == "kill"]
    assert kill["price"] == "150.25"  # the live ticker price
    assert Decimal("149") < Decimal(kill["fill_price"]) < Decimal("150.25")  # less sale costs
    assert account.state().position == 0
    status = invoke(tmp_path, "status", "btc")
    assert "HALTED: killed: drill" in status.output
    assert "2 fills" in status.output  # the buy, and the kill's sale
    refused = invoke(tmp_path, "run", "btc", "--config", REPO_CONFIG)
    assert refused.exit_code == 1
    assert "is halted (killed: drill)" in refused.output
    resumed = invoke(tmp_path, "resume", "btc", "--reason", "drill over")
    assert "btc resumed (was halted: killed: drill)" in resumed.output
    assert invoke(tmp_path, "resume", "btc", "--reason", "again").exit_code == 1  # not halted


def test_reconciliation_halts_an_exchange_account_holding_less(tmp_path: Path) -> None:
    from kestrel.config import load_config
    from tests.test_binance_venue import FakeBinance as FakeExchange
    from tests.test_binance_venue import venue

    account = PaperAccount.open(tmp_path, paper_terms("t"))
    account.ledger.append({"type": "state", "bar": T0.isoformat(), "cash": "300",
                           "position": "5", "target": "1"})  # fmt: skip
    exchange = venue(FakeExchange())  # the fake testnet account holds 1 BTC
    cli._reconcile(account, exchange, load_config(REPO_CONFIG))
    assert "reconciliation failed: BTC: ledger 5, exchange holds 1" in (
        safety.halted(account) or ""
    )
    events = len(account.ledger.events())
    cli._reconcile(account, None, load_config(REPO_CONFIG))  # the sim venue has nothing to check
    cli._reconcile(account, exchange, load_config(REPO_CONFIG))  # already halted: no second halt
    assert len(account.ledger.events()) == events


def paper_terms(name: str):  # type: ignore[no-untyped-def]
    from kestrel.execution.paper import Terms

    return Terms(name, "ma-trend", "BTCUSDT", Interval.D1, {}, Decimal(330), T0, venue="testnet")
