"""kestrel paper open / run / status, offline: a fake Binance endpoint and a frozen clock."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

import kestrel.cli as cli
from kestrel.data import live
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
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
