"""The random-timing control (kestrel.validation.timing), on the random-entry tests' cycle:
5 bars up 1% each, then 5 bars down 1% each, repeated. Holding exactly the rises is perfect
timing; the same weights at other times land partly in the falls."""

from __future__ import annotations

import itertools
import random
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.validation.log import ValidationLog
from kestrel.validation.timing import (
    MIN_OFFSET_SHARE,
    TimingControl,
    format_timing,
    offsets,
    rotate,
    run_timing_control,
)
from tests.backtest.helpers import FixedWeights, bars, config
from tests.test_control import REPO_CONFIG, cycle


def test_a_rotation_keeps_the_warm_up_and_every_weight() -> None:
    w = [0.0, 0.0, 0.5, 1.0, 0.0, 0.25]
    assert rotate(w, 1) == [0.0, 0.0, 0.25, 0.5, 1.0, 0.0]
    assert rotate(w, 4) == w  # a full turn of the four rotated bars
    assert sorted(rotate(w, 3)) == sorted(w)
    assert rotate([0.0, 0.0], 1) == [0.0, 0.0]  # never invested: nothing to rotate


def test_copies_are_never_the_strategy_shifted_a_little() -> None:
    w = [0.0] * 10 + [1.0] * 200
    got = offsets(w, 50, random.Random(1))
    assert len(set(got)) == 50
    assert min(got) >= MIN_OFFSET_SHARE * 200
    assert max(got) <= 200 - MIN_OFFSET_SHARE * 200


def walk(n: int = 400) -> tuple[list[float], list[float]]:
    """A seeded random walk, and weights that hold exactly the bars that rise (the weight at
    bar i is filled at bar i+1's open, the close of bar i, and held through bar i+1)."""
    rng, closes = random.Random(7), [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + rng.choice([-0.01, 0.01])))
    return closes, [float(b > a) for a, b in itertools.pairwise(closes)] + [0.0]


def test_good_timing_beats_its_own_weights_at_random_times() -> None:
    closes, weights = walk()
    frame = bars([closes[0], *closes[:-1]], closes=closes)
    strategy = FixedWeights(weights)
    costed = config(costs=True)
    c = run_timing_control(strategy, frame, Interval.H4, costed, Decimal(1000), paths=20)
    assert c.verdict == "BEATS RANDOM TIMING"
    assert c.beaten_share == 1
    again = run_timing_control(strategy, frame, Interval.H4, costed, Decimal(1000), paths=20)
    assert again.paths == c.paths  # seeded


def test_always_holding_has_no_timing_to_beat() -> None:
    frame = cycle(40)
    always = FixedWeights([1.0] * frame.height)
    c = run_timing_control(always, frame, Interval.H4, config(), Decimal(1000), paths=10)
    assert c.paths == pytest.approx([c.sharpe] * 10)  # every rotation is the same
    assert c.verdict == "NO BETTER THAN RANDOM"


def test_the_report() -> None:
    c = TimingControl("trend", 1.23, 0.5, 506, [0.5 + i / 100 for i in range(100)])
    text = format_timing(c, "BTCUSDT 1d")
    assert "beats random timing" in text
    assert "Information only" in text
    assert (c.beaten_share, c.verdict) == (0.73, "NO BETTER THAN RANDOM")


def test_the_command_saves_a_study_that_is_not_a_trial(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H4, cycle(60, up=1.02))
    common = ["--data-dir", str(tmp_path), "--config", REPO_CONFIG,
              "--research-dir", str(tmp_path / "r")]  # fmt: skip
    args = ["random-timing", "BTCUSDT", "--strategy", "ma-trend", "--interval", "4h",
            "--start", "2024-01", "--end", "2024-12", "--paths", "5", *common]  # fmt: skip
    got = CliRunner().invoke(app, args)
    assert got.exit_code == 0, got.output
    assert "Random-timing control" in got.output
    (saved,) = ValidationLog(tmp_path).studies()
    assert (saved["kind"], saved["looks"], saved["detail"]["paths"]) == ("random-timing", [], 5)
    brackets = CliRunner().invoke(app, [*args[:3], "channel-breakout", *args[4:]])
    assert brackets.exit_code == 1
    assert "use kestrel random-entry" in brackets.output
