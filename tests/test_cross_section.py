"""The cross-sectional funding and taker-flow studies on hand-built data: the weekly rows,
the regression with its controls, the long-only test, and the command."""

from __future__ import annotations

import math
import random
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.exploration import Split, write_split
from kestrel.research import SECTIONS, ResearchStore
from kestrel.studies.cross_section import coin_weeks, run_cross_study
from tests.backtest.helpers import bars

REPO = Path(__file__).resolve().parents[1]
DAY = timedelta(days=1)
MONDAY = datetime(2024, 1, 1, tzinfo=UTC)  # so day 6 is a Sunday


def daily(closes: list[float], volume: float = 3e7, buy_share: float = 0.5) -> pl.DataFrame:
    frame = bars(closes, step=DAY, start=MONDAY, quote_volume=volume)
    return frame.with_columns(taker_buy_quote_volume=pl.col("quote_volume") * buy_share)


def test_each_sunday_close_gets_its_past_and_next_week_and_its_signals() -> None:
    closes = [100.0 * 1.01**i for i in range(200)]
    rates = pl.DataFrame(
        {
            "time": [
                datetime(2024, 1, 7, 16, tzinfo=UTC),  # before the first Sunday's close: counts
                datetime(2024, 1, 8, 0, tzinfo=UTC),  # at the close: doesn't
            ],
            "interval_hours": [8, 8],
            "rate": [0.001, 0.5],
        }
    )
    rows = coin_weeks("XUSDT", daily(closes, buy_share=0.6), rates)
    first = rows.row(0, named=True)
    assert first["week"] == date(2024, 1, 7)  # the first Sunday
    assert first["ret_past"] is None  # no close 7 days earlier
    assert first["ret_next"] == pytest.approx(7 * math.log(1.01))
    assert first["taker"] == pytest.approx(0.6)
    assert first["funding"] == pytest.approx(0.001)
    assert not first["eligible"]  # too young
    later = rows.filter(pl.col("week") == date(2024, 6, 30)).row(0, named=True)
    assert later["eligible"]  # 180+ days and 30M a day
    assert later["ret_past"] == pytest.approx(7 * math.log(1.01))
    assert rows["ret_next"][-1] is None  # the last Sunday has no next week
    thin = coin_weeks("YUSDT", daily(closes, volume=1e6))
    assert not thin["eligible"].any()  # below 20M a day


def panel(
    effect: float, signal: str, weeks: int = 60, coins: int = 20, seed: int = 0
) -> pl.DataFrame:
    """Rows where next week's return depends on the signal by ``effect`` per unit."""
    rnd = random.Random(seed)
    rows = []
    for w in range(weeks):
        for c in range(coins):
            s = rnd.gauss(0, 1)
            rows.append(
                {
                    "week": date(2021, 1, 3) + timedelta(weeks=w),
                    "symbol": f"C{c}",
                    "eligible": True,
                    "ret_next": effect * s + rnd.gauss(0, 0.05),
                    "ret_past": rnd.gauss(0, 0.05),
                    "size": rnd.gauss(17, 1),
                    "taker": 0.5 + 0.01 * s if signal == "taker-flow" else None,
                    "funding": -0.0001 * s if signal == "funding" else None,  # low funding, high s
                }
            )
    return pl.DataFrame(rows)


def test_a_planted_effect_proceeds_and_noise_is_killed() -> None:
    found = run_cross_study(panel(0.02, "taker-flow"), "taker-flow", hurdle=0.003)
    assert found.mean_coefficient > 0
    assert found.p_coefficient >= 0.9
    assert found.verdict == "PROCEED"
    noise = run_cross_study(panel(0.0, "taker-flow"), "taker-flow", hurdle=0.003)
    assert noise.verdict == "KILL"


def test_low_funding_is_the_bullish_side() -> None:
    found = run_cross_study(panel(0.02, "funding"), "funding", hurdle=0.003)
    assert found.mean_coefficient > 0  # minus funding, so the uncrowded side scores higher
    assert found.verdict == "PROCEED"


def test_an_effect_below_the_cost_hurdle_is_killed() -> None:
    small = run_cross_study(panel(0.002, "taker-flow", weeks=300), "taker-flow", hurdle=0.05)
    assert small.p_coefficient >= 0.9
    assert small.verdict == "KILL"  # real, but not worth a round trip a week


def test_thin_weeks_are_left_out_and_an_empty_study_says_so() -> None:
    few = panel(0.02, "taker-flow", coins=5)
    with pytest.raises(ValueError, match="no week had 10"):
        run_cross_study(few, "taker-flow", hurdle=0.003)


def test_the_command_runs_a_frozen_note_on_the_exploration_half(tmp_path: Path) -> None:
    data, research = tmp_path / "data", tmp_path / "research"
    shutil.copy(REPO / "kestrel.toml", tmp_path / "kestrel.toml")
    store = ParquetStore(data)
    rnd = random.Random(3)
    names = [f"C{i}USDT" for i in range(12)]
    for i, name in enumerate(names):
        closes = [100.0]
        for _ in range(300):
            closes.append(closes[-1] * math.exp(rnd.gauss(0, 0.03)))
        store.write(name, Interval.D1, daily(closes, buy_share=0.45 + 0.01 * i))
    write_split(
        tmp_path / "research" / "exploration" / "split.toml",
        Split(date(2026, 11, 16), 1, frozenset(names), frozenset({"LOCKEDUSDT"})),
    )
    notes = ResearchStore(research)
    slug = notes.create("Taker flow").slug
    notes.edit(slug, 1, dict.fromkeys(SECTIONS, "x"), "study:taker-flow")
    notes.freeze(slug, 1)
    args = ["--research", slug, "--research-dir", str(research), "--data-dir", str(data)]
    args += ["--config", str(tmp_path / "kestrel.toml")]
    ran = CliRunner().invoke(app, ["cross-study", "taker-flow", *args])
    assert ran.exit_code == 0, ran.output
    assert "Verdict:" in ran.output
    assert "12-12 eligible coins a week" in ran.output
    assert ResearchStore(research).get(slug).status == "backtested"
    wrong = CliRunner().invoke(app, ["cross-study", "funding", *args])
    assert wrong.exit_code == 1
    assert "not study:funding-cross-section" in wrong.output
