"""Worked examples of a strategy's rule (kestrel.strategies.examples): random entries and
exits on an exploration-half coin, each with the weight chosen and the tests behind it."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kestrel.api.app import create_app
from kestrel.cli import app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.exploration import SPLIT_FILE, Split, write_split
from kestrel.strategies.examples import Example, examples, format_example, require_exploration
from kestrel.strategies.trend import TrendVolTarget
from tests.backtest.helpers import bars

REPO = Path(__file__).resolve().parents[1]
SPLIT = Split(date(2026, 11, 16), 1, frozenset({"EXPUSDT"}), frozenset({"CONUSDT"}))


def wavy(n: int = 600) -> pl.DataFrame:
    """Long swings up and down, so the trend rule enters and exits many times."""
    closes = [100 * math.exp(0.4 * math.sin(i / 40) + 0.002 * math.sin(i)) for i in range(n)]
    return bars(closes, start=datetime(2021, 1, 1, tzinfo=UTC), step=timedelta(days=1))


def test_examples_are_drawn_entries_and_exits_whose_tests_agree_with_the_weight() -> None:
    frame = wavy()
    s = TrendVolTarget("EXPUSDT")
    found = examples(s, frame)
    assert len(found) == 3
    assert {e.kind for e in found} == {"entry", "exit"}  # at least one of each
    assert [e.open_time for e in found] == sorted(e.open_time for e in found)
    assert examples(s, frame) == found  # seeded
    assert examples(s, frame, seed=1) != found
    for e in found:
        assert (e.before > 0) != (e.after > 0)
        assert e.rule == "any"
        assert e.agrees is True
    everything = examples(s, frame, count=10_000)
    assert all(e.agrees for e in everything)  # every entry and exit, not only the drawn ones


def test_a_disagreement_is_flagged_and_a_strategy_without_tests_says_so() -> None:
    e = examples(TrendVolTarget("EXPUSDT"), wavy())[0]
    flipped = replace(e, after=0.0 if e.after > 0 else 0.5)
    assert flipped.agrees is False
    assert "DISAGREE: a bug" in format_example("EXPUSDT", flipped)
    bare = replace(e, rule=None, conditions=[])
    assert bare.agrees is None
    assert "no tests to read" in format_example("EXPUSDT", bare)
    assert isinstance(e, Example)


def test_only_exploration_half_coins() -> None:
    require_exploration("expusdt", SPLIT)
    for symbol in ("CONUSDT", "BTCUSDT"):
        with pytest.raises(ValueError, match="exploration-half coins only"):
            require_exploration(symbol, SPLIT)
    with pytest.raises(ValueError, match="exploration-half coins only"):
        require_exploration("EXPUSDT", None)


def test_the_command_and_the_endpoint(tmp_path: Path) -> None:
    (tmp_path / "kestrel.toml").write_text((REPO / "kestrel.toml").read_text())
    write_split(tmp_path / SPLIT_FILE, SPLIT)
    data = tmp_path / "data"
    ParquetStore(data).write("EXPUSDT", Interval.D1, wavy())
    ParquetStore(data).write("BTCUSDT", Interval.D1, wavy())
    config = str(tmp_path / "kestrel.toml")
    base = ["--data-dir", str(data), "--config", config]
    got = CliRunner().invoke(app, ["strategy-examples", "trend-vol-target", "EXPUSDT", *base])
    assert got.exit_code == 0, got.output
    assert got.output.count("EXPUSDT  ·  ") == 3
    assert "Tests and weight  agree" in got.output
    refused = CliRunner().invoke(app, ["strategy-examples", "trend-vol-target", "BTCUSDT", *base])
    assert refused.exit_code == 1

    client = TestClient(create_app(data_dir=data, config_path=config))
    res = client.post("/api/strategies/trend-vol-target/examples", json={"symbol": "EXPUSDT"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 3
    assert all(e["agrees"] for e in body)
    assert body[0]["conditions"][0]["label"].startswith("EXP up over")
    no = client.post("/api/strategies/trend-vol-target/examples", json={"symbol": "BTCUSDT"})
    assert no.status_code == 422
