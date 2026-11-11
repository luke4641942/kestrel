"""The anchor-day range study (backlog S1, "Monday range"), on synthetic hourly bars.

Each week of the planted path, in AEST (UTC+10):

    Monday     closes alternate 99.6 / 100.4: range 99.55 to 100.45, midpoint 100.00
    Tuesday    up to 101 by 05:00, breaking Monday's high at 00:00 (close 100.5); the
               01:00 bar's low (100.45) is already within 0.5% of the midpoint, so the
               pullback buy is 02:00's open (100.6); then a dip to 100.2 and a steady
               rise to 106 by Saturday night
    Sunday     back down to 99.6, where the next Monday starts

So Monday's range always breaks up first and its pullback, held to Friday's close, is a
winning buy, while the other days' setups run into Sunday's fall. The study must say
SURVIVES here, and KILL on a random walk that has no weekday effect at all.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.studies.anchor_day import (
    StudyResult,
    _measure,
    anchor_outcomes,
    format_study,
    run_study,
)
from tests.backtest.helpers import bars

MONDAY_AEST = datetime(2024, 1, 7, 14, tzinfo=UTC)  # Monday 8 Jan 2024, 00:00 in Brisbane
HOUR = timedelta(hours=1)
REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")


def _line(a: float, b: float, n: int) -> list[float]:
    """n closes stepping evenly from just after a to b."""
    return [a + (b - a) * (k + 1) / n for k in range(n)]


def planted_week() -> list[float]:
    monday = [99.6 if h % 2 == 0 else 100.4 for h in range(24)]
    tuesday_up = _line(100.4, 101.0, 6)
    tuesday_dip = _line(101.0, 100.2, 6)
    rise = _line(100.2, 106.0, 12 + 24 * 4)  # rest of Tuesday through Saturday
    sunday = _line(106.0, 99.6, 24)
    week = monday + tuesday_up + tuesday_dip + rise + sunday
    assert len(week) == 168
    return week


def hourly(closes: list[float], start: datetime = MONDAY_AEST) -> pl.DataFrame:
    opens = [closes[0], *closes[:-1]]
    highs = [max(o, c) + 0.05 for o, c in zip(opens, closes, strict=True)]
    lows = [min(o, c) - 0.05 for o, c in zip(opens, closes, strict=True)]
    return bars(opens, closes=closes, highs=highs, lows=lows, start=start, step=HOUR)


def planted(weeks: int = 30) -> pl.DataFrame:
    return hourly(planted_week() * weeks)


def random_walk_hours(weeks: int = 60, seed: int = 5) -> pl.DataFrame:
    rnd = random.Random(seed)
    closes, price = [], 100.0
    for _ in range(168 * weeks):
        price *= math.exp(rnd.gauss(0, 0.004))
        closes.append(price)
    return hourly(closes)


# ---------- one anchor, by hand ----------


def anchor_and_horizon(closes: list[float], lows: list[float] | None = None):  # type: ignore[no-untyped-def]
    anchor = pl.DataFrame({"high": [110.0], "low": [100.0], "open": [105.0], "close": [105.0]})
    n = len(closes)
    horizon = pl.DataFrame(
        {
            "open": [105.0, *closes[:-1]],
            "high": [c + 0.1 for c in closes],
            "low": lows or [c - 0.1 for c in closes],
            "close": closes,
        }
    )
    assert horizon.height == n
    return anchor, horizon


def test_up_first_with_a_pullback_to_the_midpoint() -> None:
    # Range 100-110, midpoint 105. Bar 1 closes above 110; bar 3 dips to 105.3 (within
    # 0.5% of 105); buy bar 4's open (106) and sell at the last close (112).
    closes = [108.0, 111, 109, 105.4, 106, 112]
    lows = [107.9, 110.9, 108.9, 105.3, 105.9, 111.9]
    o = _measure(date(2024, 1, 8), *anchor_and_horizon(closes, lows), 0.005)
    assert o.first == "up"
    assert o.weekday == 0
    assert o.continuation == pytest.approx(112 / 111 - 1)
    assert o.pullback == pytest.approx(112 / 105.4 - 1)  # bar 4 opens at bar 3's close


def test_down_first_has_no_pullback_trade() -> None:
    o = _measure(date(2024, 1, 9), *anchor_and_horizon([104.0, 99.0, 101.0]), 0.005)
    assert o.first == "down"
    assert o.continuation == pytest.approx(101 / 99 - 1)
    assert o.pullback is None
    assert o.week == date(2024, 1, 8)  # Tuesday belongs to Monday's week


def test_no_break() -> None:
    o = _measure(date(2024, 1, 8), *anchor_and_horizon([104.0, 106.0]), 0.005)
    assert (o.first, o.continuation, o.pullback) == ("none", None, None)


def test_a_pullback_on_the_last_bar_cannot_be_bought() -> None:
    o = _measure(date(2024, 1, 8), *anchor_and_horizon([111.0, 105.0]), 0.005)
    assert o.first == "up"
    assert o.pullback is None  # no next open inside the horizon


# ---------- days and alignment ----------


def test_days_are_brisbane_days() -> None:
    outcomes = anchor_outcomes(planted(3))
    # 21 days, of which the last 4 have no complete 4-day horizon.
    assert [o.day for o in outcomes][:2] == [date(2024, 1, 8), date(2024, 1, 9)]
    assert len(outcomes) == 21 - 4
    assert outcomes[0].weekday == 0


def test_incomplete_days_are_skipped() -> None:
    frame = planted(3)
    gappy = frame.filter(pl.col("open_time") != MONDAY_AEST + 3 * HOUR)  # a Monday hour
    days = [o.day for o in anchor_outcomes(gappy)]
    assert date(2024, 1, 8) not in days


def test_bars_longer_than_an_hour_are_refused() -> None:
    four_hourly = bars([100.0] * 50, step=timedelta(hours=4))
    with pytest.raises(ValueError, match="1h or shorter"):
        anchor_outcomes(four_hourly)


# ---------- verdicts ----------


def test_a_planted_monday_effect_survives() -> None:
    r = run_study(planted(), "TEST", resamples=200)
    monday = r.rows[0]
    assert monday.up_first == 1.0
    friday_close = _line(100.2, 106.0, 108)[83]  # Tuesday 12:00 + 83 hours = Friday 23:00
    assert monday.pullback_return == pytest.approx(friday_close / 100.6 - 1)
    assert r.rows[5].up_first == 0.0  # Saturday's range breaks down into Sunday
    assert r.survives
    assert "SURVIVES" in format_study(r)


def test_a_random_walk_is_killed() -> None:
    r = run_study(random_walk_hours(), "TEST", resamples=500)
    assert not r.survives
    assert "KILL" in format_study(r)


def test_the_verdict_needs_all_three_conditions() -> None:
    base = run_study(planted(), "TEST", resamples=50)
    assert base.survives

    def but(**changes: tuple[float, float]) -> StudyResult:
        return StudyResult(**{**base.__dict__, **changes})

    assert not but(up_first_diff_ci=(-0.01, 0.2)).survives
    assert not but(pullback_diff_ci=(-0.001, 0.05)).survives
    assert not but(monday_pullback_ci=(0.001, 0.05)).survives  # under the 0.22% hurdle


def test_no_mondays_is_an_error() -> None:
    tuesday_start = hourly(planted_week() * 2, start=MONDAY_AEST + 24 * HOUR)
    with pytest.raises(ValueError, match="no complete Mondays"):
        run_study(tuesday_start.head(24 * 6), "TEST")


# ---------- command line ----------

runner = CliRunner()


def test_cli_runs_the_study(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.H1, planted(8))
    args = ["anchor-study", "BTCUSDT", "--start", "2024-01", "--end", "2024-03"]
    result = runner.invoke(app, [*args, "--data-dir", str(tmp_path), "--config", REPO_CONFIG])
    assert result.exit_code == 0, result.output
    assert "Monday against the other days" in result.output
    assert "hurdle 0.22%" in result.output


def test_cli_never_uses_the_holdout(tmp_path: Path) -> None:
    args = ["anchor-study", "BTCUSDT", "--start", "2025-06", "--end", "2025-11"]
    result = runner.invoke(app, [*args, "--data-dir", str(tmp_path), "--config", REPO_CONFIG])
    assert result.exit_code == 1
    assert "holdout" in result.output


def test_cli_reports_unusable_data(tmp_path: Path) -> None:
    args = ["anchor-study", "BTCUSDT", "--start", "2024-01", "--end", "2024-01"]
    result = runner.invoke(app, [*args, "--data-dir", str(tmp_path), "--config", REPO_CONFIG])
    assert result.exit_code == 1


def test_a_flat_market_has_no_breaks_and_no_verdict_in_its_favour() -> None:
    # Closes never leave the anchor range: no breaks, no pullbacks, so nothing to compare.
    r = run_study(hourly([100.0] * 168 * 3), "TEST", resamples=50)
    assert r.rows[0].up_first is None
    assert r.rows[0].pullbacks == 0
    assert math.isnan(r.up_first_diff_ci[0])
    assert not r.survives
    assert "n/a" in format_study(r)


def test_cli_reports_a_bad_config(tmp_path: Path) -> None:
    args = ["anchor-study", "BTCUSDT", "--start", "2024-01", "--end", "2024-01"]
    result = runner.invoke(
        app, [*args, "--data-dir", str(tmp_path), "--config", str(tmp_path / "none.toml")]
    )
    assert result.exit_code == 1
