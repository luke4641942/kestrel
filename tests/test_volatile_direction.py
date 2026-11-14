"""The volatile-spell direction study, on synthetic paths whose answer is known."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data.features import REGIME_VOLATILE
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.studies.volatile_direction import (
    Sample,
    StudyResult,
    _side,
    format_study,
    run_study,
    samples,
)
from tests.test_regime_strategy import store_fit
from tests.test_volhold_weekly import daily

REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")
T = datetime(2024, 1, 1, tzinfo=UTC)
HURDLE = math.log1p(0.0022)


def flagged(frame: pl.DataFrame, flags: list[float | None]) -> pl.DataFrame:
    return frame.with_columns(pl.Series(REGIME_VOLATILE, flags, dtype=pl.Float64))


def test_samples_are_a_horizon_apart_and_only_from_the_chosen_regime() -> None:
    frame = flagged(daily([0.01] * 99), [None] * 40 + [1.0] * 30 + [0.0] * 29 + [1.0])
    vol = samples(frame, "X", 7, volatile=True)
    days = [frame["open_time"].to_list().index(s.day) for s in vol]
    assert days == [40, 47, 54, 61, 68]  # every 7th volatile day, none overlapping
    assert all(s.forward == pytest.approx(7 * 0.01) for s in vol)
    calm = samples(frame, "X", 7, volatile=False)
    assert [frame["open_time"].to_list().index(s.day) for s in calm] == [70, 77, 84, 91]
    # The last day (99) is volatile but has no week after it: left out.


def trending(n: int, seed: int) -> pl.DataFrame:
    """60-day trends up and down: MACD's sign mostly agrees with the next week."""
    rnd = random.Random(seed)
    rets = [(0.02 if (i // 60) % 2 == 0 else -0.02) + rnd.gauss(0, 0.005) for i in range(n)]
    return flagged(daily(rets), [1.0] * (n + 1))


def choppy(n: int, seed: int) -> pl.DataFrame:
    """Every 8-day move reverses the next: whatever MACD says, the next week goes back."""
    rnd = random.Random(seed)
    rets = [(0.03 if (i // 8) % 2 == 0 else -0.03) + rnd.gauss(0, 0.002) for i in range(n)]
    return flagged(daily(rets), [1.0] * (n + 1))


def test_the_verdict_says_which_way() -> None:
    trend = run_study({"A": trending(900, 1), "B": trending(900, 2)}, hurdle=HURDLE, resamples=500)
    assert trend.verdict == "TREND"
    assert trend.volatile.spread is not None
    assert trend.volatile.spread > HURDLE
    noise = daily([random.Random(3).gauss(0, 0.03) for _ in range(900)])
    killed = run_study({"A": flagged(noise, [1.0] * 901)}, hurdle=10.0, resamples=200)
    assert killed.verdict == "KILL"  # whatever the spread, it can't clear a 10x hurdle


def test_a_rebound_reads_as_rebound() -> None:
    s = [Sample("A", T, up, f) for up, f in [(True, -0.05)] * 20 + [(False, 0.05)] * 20]
    side = _side(s, 300, 0)
    r = StudyResult(["A"], 7, HURDLE, side, side)
    assert r.verdict == "REBOUND"
    assert side.spread == pytest.approx(-0.10)
    assert "REBOUND" in format_study(r)


def test_one_sided_or_empty_samples_kill() -> None:
    only_up = _side([Sample("A", T, True, 0.1)], 100, 0)
    assert (only_up.spread, only_up.p_positive) == (None, None)
    assert StudyResult(["A"], 7, HURDLE, only_up, only_up).verdict == "KILL"
    assert "n/a" in format_study(StudyResult(["A"], 7, HURDLE, only_up, only_up))


def test_the_cli_uses_stored_regimes_and_never_the_holdout(tmp_path: Path) -> None:
    start = datetime(2025, 3, 1, tzinfo=UTC)
    rets = [(0.02 if (i // 40) % 2 == 0 else -0.02) for i in range(300)]
    frame = daily(rets)
    frame = frame.with_columns(pl.col("open_time") - (frame["open_time"][0] - start))
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, frame)
    pre = frame.filter(pl.col("open_time") < datetime(2025, 11, 1, tzinfo=UTC))
    store_fit(tmp_path, pre, [2] * pre.height)  # every pre-holdout day volatile
    out = CliRunner().invoke(
        app, ["volatile-study", "btcusdt", "--data-dir", str(tmp_path), "--config", REPO_CONFIG]
    )
    assert out.exit_code == 0, out.output
    assert "BTCUSDT 1d" in out.output
    assert "Hurdle: 0.22%" in out.output
    missing = CliRunner().invoke(
        app, ["volatile-study", "ETHUSDT", "--data-dir", str(tmp_path), "--config", REPO_CONFIG]
    )
    assert missing.exit_code == 1
    assert pre["open_time"].max() < datetime(2025, 11, 1, tzinfo=UTC)
    assert timedelta(days=1) == frame["open_time"].diff().max()


def test_a_confident_spread_too_small_to_pay_costs_is_killed() -> None:
    tiny = [Sample("A", T, True, 0.001)] * 30 + [Sample("A", T, False, 0.0)] * 30
    side = _side(tiny, 200, 0)
    assert side.p_positive == 1.0  # certain, but 0.1% a week doesn't pay 0.22% of costs
    assert StudyResult(["A"], 7, HURDLE, side, side).verdict == "KILL"


def study_note(root: Path, implementation: str = "study:volatile-direction") -> str:
    from kestrel.research import ResearchStore

    store = ResearchStore(root)
    s = store.create("A study")
    sections = ("hypothesis", "rationale", "rules", "falsification", "parameters", "costs")
    store.edit(s.slug, 1, dict.fromkeys(sections, "x"), implementation)
    store.freeze(s.slug, 1)
    return s.slug


def test_a_note_can_name_a_study_which_a_backtest_refuses(tmp_path: Path) -> None:
    from kestrel.research import ResearchError, ResearchStore, run_target

    slug = study_note(tmp_path)
    with pytest.raises(
        ResearchError, match="is a study, not a strategy: run it with kestrel volatile-study"
    ):
        run_target(ResearchStore(tmp_path), "ma-trend", slug)
    with pytest.raises(ResearchError, match="unknown implementation"):
        ResearchStore(tmp_path).edit(ResearchStore(tmp_path).create("B").slug, 1, {}, "study:nope")


def test_the_cli_runs_under_its_frozen_note(tmp_path: Path) -> None:
    from kestrel.research import ResearchStore

    frame = flagged(daily([0.01] * 99), [1.0] * 100)
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, frame.drop(REGIME_VOLATILE))
    store_fit(tmp_path, frame, [2] * 100)
    slug = study_note(tmp_path / "r")
    args = ["volatile-study", "BTCUSDT", "--data-dir", str(tmp_path), "--config", REPO_CONFIG,
            "--research-dir", str(tmp_path / "r")]  # fmt: skip
    ok = CliRunner().invoke(app, [*args, "--research", slug])
    assert ok.exit_code == 0, ok.output
    assert ResearchStore(tmp_path / "r").get(slug).status == "backtested"
    other = study_note(tmp_path / "r2", implementation="ma-trend")
    args[-1] = str(tmp_path / "r2")
    wrong = CliRunner().invoke(app, [*args, "--research", other])
    assert wrong.exit_code == 1
    assert "names ma-trend, not this study" in wrong.output
