"""The random-entry control, on a hand-built cycle: 5 bars up 1% each, then 5 bars down
1% each, repeated. A strategy that buys the first up bar (+3% target, -3% stop) catches
every rise; random entries land in the falls half the time and get stopped."""

from __future__ import annotations

import random
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.validation.control import (
    Bracket,
    Control,
    RandomEntries,
    brackets_of,
    format_control,
    random_strategy,
    run_control,
)
from kestrel.validation.log import ValidationLog
from tests.backtest.helpers import bars, config

REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")


def cycle(n_cycles: int = 30, up: float = 1.01) -> pl.DataFrame:
    closes, price = [100.0], 100.0
    for _ in range(n_cycles):
        for step in [up] * 5 + [0.99] * 5:
            price *= step
            closes.append(price)
    return bars([closes[0], *closes[:-1]], closes=closes)


class TurnUp:
    """Buy the first up bar after a fall."""

    name, symbol = "turn up", "BTCUSDT"

    def brackets(self, b: pl.DataFrame) -> pl.DataFrame:
        c = b["close"].to_list()
        enter = [i >= 2 and c[i] > c[i - 1] < c[i - 2] for i in range(len(c))]
        return pl.DataFrame(
            {
                "enter": enter,
                "stop": [x * 0.97 if e else None for x, e in zip(c, enter, strict=True)],
                "target": [x * 1.03 if e else None for x, e in zip(c, enter, strict=True)],
            },
            schema={"enter": pl.Boolean, "stop": pl.Float64, "target": pl.Float64},
        )


def test_brackets_are_kept_as_fractions_of_the_close() -> None:
    b = cycle(3)
    rows, shapes = brackets_of(TurnUp().brackets(b), b)
    assert rows == [11, 21]  # the first up bar after each fall
    assert shapes[0] == Bracket(pytest.approx(0.03), pytest.approx(0.03))  # type: ignore[arg-type]


def test_random_copies_keep_the_count_and_shapes_but_not_the_times() -> None:
    b = cycle(10)
    rows, shapes = brackets_of(TurnUp().brackets(b), b)
    copy = random_strategy(TurnUp(), rows, shapes, b.height, random.Random(1))
    assert len(copy.entries) == len(rows)
    assert min(copy.entries) >= rows[0]
    assert max(copy.entries) < b.height - 1
    assert sorted(copy.entries) != rows
    assert sorted(x.stop for x in copy.entries.values()) == sorted(x.stop for x in shapes)
    frame = copy.brackets(b)
    i = min(copy.entries)
    assert frame["stop"][i] == pytest.approx(b["close"][i] * 0.97)
    assert copy.brackets(b.head(i)).height == i  # a truncated history knows only its rows
    with pytest.raises(ValueError, match="never signals"):
        random_strategy(TurnUp(), [], [], b.height, random.Random(1))


def test_a_real_edge_beats_random_entries_and_reruns_identically() -> None:
    from decimal import Decimal

    b = cycle()
    got = run_control(TurnUp(), b, Interval.H4, config(), Decimal(10_000), paths=20)
    assert got.expectancy_r is not None
    assert got.expectancy_r > 0.5
    assert got.beaten_share == 1.0
    assert got.verdict == "BEATS RANDOM"
    again = run_control(TurnUp(), b, Interval.H4, config(), Decimal(10_000), paths=20)
    assert again.paths == got.paths


def test_the_verdict() -> None:
    paths: list[float | None] = [i / 100 for i in range(100)]
    assert Control("s", 0.97, 40, paths).verdict == "BEATS RANDOM"  # beats 97 of 100
    assert Control("s", 0.90, 40, paths).verdict == "NO BETTER THAN RANDOM"
    assert Control("s", None, 0, paths).verdict == "NO TRADES"
    report = format_control(Control("s", 0.97, 40, [*paths, None]), "BTCUSDT 4h")
    assert "100 of 101 paths traded" in report
    assert "97% of random paths (95% needed)" in report


def test_random_entries_are_a_bracket_strategy() -> None:
    from kestrel.strategy import BracketStrategy

    assert isinstance(RandomEntries("r", "BTCUSDT", {}), BracketStrategy)


def test_the_command_saves_a_study(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H4, cycle(60, up=1.02))  # new highs
    common = ["--data-dir", str(tmp_path), "--config", REPO_CONFIG,
              "--research-dir", str(tmp_path / "r")]  # fmt: skip
    args = ["random-entry", "BTCUSDT", "--strategy", "channel-breakout", "--interval", "4h",
            "--start", "2024-01", "--end", "2024-12", "--paths", "5", *common]  # fmt: skip
    got = CliRunner().invoke(app, args)
    assert got.exit_code == 0, got.output
    assert "Random-entry control" in got.output
    (saved,) = ValidationLog(tmp_path).studies()
    assert saved["kind"] == "random-entry"
    assert saved["looks"] == []  # not trials
    assert saved["detail"]["paths"] == 5
    weights = CliRunner().invoke(app, [*args[:3], "ma-trend", *args[4:]])
    assert weights.exit_code == 1
    assert "holds weights, not brackets" in weights.output
