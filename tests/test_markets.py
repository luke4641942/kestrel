"""Markets (kestrel.markets): every decision, note and run says which market it belongs to,
and the API serves each market's trial pool and the forex runs."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from kestrel.api.app import create_app
from kestrel.backtest.fx_runs import FxRunStore, compute_fx_metrics
from kestrel.decisions import load_decisions
from kestrel.markets import asset_class
from kestrel.research import SECTIONS, ResearchStore
from tests.test_decisions import entry, write

REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")


def test_the_market_is_read_from_the_implementation() -> None:
    assert asset_class("fx:trend") == "forex"
    assert asset_class("trend-vol-target") == "crypto"
    assert asset_class("study:delistings") == "crypto"
    assert asset_class(None) == "crypto"


def test_a_forex_strategy_can_be_decided(tmp_path: Path) -> None:
    (d,) = load_decisions(write(tmp_path, entry(strategy="fx:trend", decision="watch")))
    assert (d.strategy, d.decision) == ("fx:trend", "watch")


def test_the_api_labels_markets_and_serves_forex_runs(tmp_path: Path) -> None:
    research = tmp_path / "research"
    research.mkdir()
    (research / "decisions.toml").write_text(entry() + entry(strategy="fx:trend", decision="watch"))
    store = ResearchStore(research)
    for name, key in (("A crypto note", "trend-vol-target"), ("A forex note", "fx:trend")):
        slug = store.create(name).slug
        store.edit(slug, 1, dict.fromkeys(SECTIONS, "x"), key)

    from kestrel.backtest.fx_engine import run_fx
    from kestrel.data import fx
    from kestrel.data.rates import Rates
    from tests.test_fx_backtest import Following, fx_config, write_data

    data = tmp_path
    write_data(data)
    cfg = fx_config()
    bars = {"EURAUD": fx.load(data, "EURAUD")}
    weights = Following().target_weights(bars, Rates({}))
    result = run_fx("follows", bars, weights, cfg.fx, 0.02, Rates.load(data), 500)
    FxRunStore(data).save(
        result, compute_fx_metrics(result), params={}, start="2010-01", end="2010-06",
        research={"slug": "a-forex-note", "version": 1},
    )  # fmt: skip

    client = TestClient(
        create_app(data, static_dir=None, research_dir=research, config_path=REPO_CONFIG)
    )
    classes = {d["strategy"]: d["asset_class"] for d in client.get("/api/decisions").json()}
    assert classes == {"trend-vol-target": "crypto", "fx:trend": "forex"}
    notes = {n["name"]: n["asset_class"] for n in client.get("/api/research").json()}
    assert notes == {"A crypto note": "crypto", "A forex note": "forex"}
    v = client.get("/api/validation").json()
    assert v["pools"]["forex"] == 1
    assert set(v["pool_bars"]) == {"main", "confirmation", "forex"}
    (run,) = client.get("/api/fx-runs").json()
    assert run["asset_class"] == "forex"
    assert run["deflated_trials"] == 1
    assert run["research"] == {"slug": "a-forex-note", "version": 1}
    assert "sharpe" in run["metrics"]
