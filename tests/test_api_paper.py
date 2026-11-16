"""The paper trading API: read-only views of accounts opened and run from the CLI."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.config import load_config
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.execution import safety
from kestrel.execution.paper import PaperAccount, Terms, run
from tests.backtest.helpers import T0
from tests.test_paper import walk

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"


def test_accounts_are_listed_and_shown_with_their_tracking(tmp_path: Path) -> None:
    frame = walk(120)
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, frame)
    client = TestClient(create_app(tmp_path, static_dir=None, config_path=REPO_CONFIG))
    assert client.get("/api/paper").json() == []

    terms = Terms(
        "btc", "ma-trend", "BTCUSDT", Interval.D1, {"fast": 5, "slow": 20}, Decimal(330), T0
    )
    account = PaperAccount.open(tmp_path, terms)
    run(account, frame, load_config(REPO_CONFIG))  # the API's model replay uses the repo config
    (summary,) = client.get("/api/paper").json()
    assert (summary["name"], summary["venue"], summary["halted"]) == ("btc", "sim", None)
    assert summary["equity"] is not None

    d = client.get("/api/paper/btc").json()
    n = frame.height - 1
    assert len(d["equity_series"]["time"]) == n == len(d["expected"]["equity"])
    assert d["equity_series"]["equity"] == pytest.approx(d["expected"]["equity"])  # sim = model
    assert d["equity_series"]["time"][0] == int(T0.timestamp()) + 86_400  # marked at the close
    assert d["tracking"]["decision_mismatches"] == 0
    assert d["tracking"]["difference"] == 0
    assert d["fills"]
    assert d["fills"][0]["cost_bps"] == pytest.approx(d["fills"][0]["expected_bps"])
    assert d["decisions"][0]["bar"] > d["decisions"][-1]["bar"]  # newest first

    safety.halt(account, "testing")
    d = client.get("/api/paper/btc").json()
    assert d["halted"] == "testing"
    assert d["events"][-1] == {"type": "halt", "at": d["events"][-1]["at"], "reason": "testing"}
    assert client.get("/api/paper/nobody").status_code == 404


def test_an_account_with_nothing_handled_yet(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, walk(30))
    client = TestClient(create_app(tmp_path, static_dir=None, config_path=REPO_CONFIG))
    PaperAccount.open(
        tmp_path, Terms("new", "ma-trend", "BTCUSDT", Interval.D1, {}, Decimal(330), T0)
    )
    d = client.get("/api/paper/new").json()
    assert (d["equity"], d["equity_series"]["time"], d["tracking"]["days"]) == (None, [], 0)
    assert d["tracking"]["cost_bps"] is None


def test_a_basket_account_shows_its_coins_targets_and_tracks_the_model(tmp_path: Path) -> None:
    import polars as pl

    from kestrel.data.schema import KLINE_SCHEMA
    from kestrel.execution.paper import run_basket
    from tests.test_btc_rotation import MONDAY, coins

    panel = coins()
    store = ParquetStore(tmp_path)
    for (sym,), coin in panel.partition_by("symbol", as_dict=True).items():
        missing = [pl.lit(0).cast(t).alias(c) for c, t in KLINE_SCHEMA.items() if c not in coin]
        store.write(str(sym), Interval.D1, coin.with_columns(missing).select(list(KLINE_SCHEMA)))
    terms = Terms("rot", "btc-rotation-4", "UNIVERSE", Interval.D1, {}, Decimal(330), MONDAY)
    account = PaperAccount.open(tmp_path, terms)
    run_basket(account, panel, load_config(REPO_CONFIG))
    client = TestClient(create_app(tmp_path, static_dir=None, config_path=REPO_CONFIG))
    d = client.get("/api/paper/rot").json()
    assert d["tracking"]["decision_mismatches"] == 0
    assert d["tracking"]["difference"] == 0  # the simulated venue is the model
    assert d["equity_series"]["equity"] == pytest.approx(d["expected"]["equity"])
    assert {f["symbol"] for f in d["fills"]} <= {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}
    assert all(f["expected_bps"] == pytest.approx(f["cost_bps"]) for f in d["fills"])
    assert d["decisions"]
    assert all(x["targets"] is not None for x in d["decisions"])  # weekly decisions only
    held = next(x for x in d["decisions"] if x["targets"])
    assert held["target"] == pytest.approx(sum(w or 0 for w in held["targets"].values()))
    assert held["current"] is None
    assert d["positions"] == {
        k: pytest.approx(float(v)) for k, v in account.state().positions.items()
    }
