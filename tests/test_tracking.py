"""Tracking a paper account against its replay on the simulated venue (the backtest model)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from kestrel.data.schema import Interval
from kestrel.execution.paper import PaperAccount, Terms, run
from kestrel.execution.tracking import format_tracking, track
from tests.backtest.helpers import T0, bars, config
from tests.test_binance_venue import FakeBinance, venue
from tests.test_paper import walk

D = Decimal
MA = {"fast": 5, "slow": 20}


def test_a_simulated_account_tracks_its_model_exactly(tmp_path: Path) -> None:
    frame = walk(200)
    paper = PaperAccount.open(
        tmp_path, Terms("sim", "ma-trend", "BTCUSDT", Interval.D1, MA, D(1000), T0)
    )
    run(paper, frame, config())
    t = track(paper, frame, config())
    assert len(t.days) == frame.height - 1
    assert t.decision_mismatches == 0
    assert t.difference == 0
    assert t.missed == 0
    assert t.fills
    assert all(f.cost_bps == f.expected_bps for f in t.fills)
    text = format_tracking("sim", t)
    assert f"{len(t.days)} of {len(t.days)} match the model" in text
    assert "(+0.00%)" in text


def test_real_fills_are_measured_against_the_model(tmp_path: Path) -> None:
    closes = [100.0 + i for i in range(40)]
    frame = bars([closes[0], *closes[:-1]], closes=closes, step=timedelta(days=1))
    latest = frame["open_time"][-1]
    terms = Terms("t", "ma-trend", "BTCUSDT", Interval.D1, MA, D(330), latest, venue="testnet")
    paper = PaperAccount.open(tmp_path, terms)
    run(paper, frame, config(), lambda _: D(100), venue=venue(FakeBinance()))
    t = track(paper, frame, config())
    (fill,) = t.fills
    # The fake fills 0.5 over the 100 reference, and charges 0.00001 BTC on the buy.
    qty = D("3.2")
    expected_cost = (D("0.5") * qty + D("0.00001") * D("100.5")) / (qty * 100) * 10_000
    assert fill.cost_bps == pytest.approx(expected_cost, rel=D("0.01"))
    assert fill.expected_bps is not None
    assert fill.expected_bps < fill.cost_bps  # the model expected about 11 bps, not 50
    assert t.decision_mismatches == 0
    assert "cost 50." in format_tracking("t", t)


def test_missed_days_are_counted(tmp_path: Path) -> None:
    closes = [100.0 + i for i in range(40)]
    frame = bars([closes[0], *closes[:-1]], closes=closes, step=timedelta(days=1))
    start = frame["open_time"][35]
    terms = Terms("m", "ma-trend", "BTCUSDT", Interval.D1, MA, D(330), start, venue="testnet")
    paper = PaperAccount.open(tmp_path, terms)
    run(paper, frame, config(), lambda _: D(139), venue=venue(FakeBinance()))
    t = track(paper, frame, config())
    assert t.missed == 4
    assert "Missed days       4" in format_tracking("m", t)


def test_nothing_to_track_yet(tmp_path: Path) -> None:
    paper = PaperAccount.open(
        tmp_path, Terms("n", "ma-trend", "BTCUSDT", Interval.D1, MA, D(1), T0)
    )
    t = track(paper, walk(10), config())
    assert (t.days, t.fills, t.difference, t.mean_cost()) == ([], [], None, None)
    assert format_tracking("n", t) == "n: nothing to track yet"
