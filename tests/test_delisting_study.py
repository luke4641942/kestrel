"""The delisting study on hand-checkable synthetic prices: paths, grouping, verdicts, CLI."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data import delistings
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.research import SECTIONS, ResearchStore
from kestrel.studies.delisting import (
    CoinPath,
    coin_path,
    events,
    format_delisting_study,
    run_delisting_study,
    share_above_zero,
)
from tests.backtest.helpers import bars

REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")
START = datetime(2024, 1, 1, tzinfo=UTC)
DAY0 = 60  # 2024-03-01: the announcement day
REMOVAL = 70  # 2024-03-11
ANNOUNCED = START + timedelta(days=DAY0, hours=6)
REMOVED = START + timedelta(days=REMOVAL, hours=3)
TIERS = [(500e6, 1e-4), (100e6, 3e-4), (20e6, 8e-4), (0.0, 15e-4)]
FEE = 7.5e-4


def daily(closes: list[float], volume: float = 1e6, start: datetime = START) -> pl.DataFrame:
    """Each day opens at the previous close, so open of day i is close of day i - 1."""
    return bars(
        [closes[0], *closes[:-1]],
        closes=closes,
        start=start,
        step=timedelta(days=1),
        quote_volume=volume,
    )


def crash_then_rebound() -> list[float]:
    closes = [1.0] * 80
    closes[DAY0] = 0.5  # the announcement day halves it
    for i in range(DAY0 + 1, 80):
        closes[i] = 0.5 + 0.1 * min(i - DAY0, 9) / 9  # back to 0.6 by day 69
    return closes


def measure(coin_bars: pl.DataFrame, btc: pl.DataFrame | None = None, **over: object) -> object:
    kwargs: dict[str, object] = {"announced_at": ANNOUNCED, "removal_at": REMOVED}
    kwargs.update(over)
    return coin_path(
        "ABC",
        "a1",
        kwargs["announced_at"],  # type: ignore[arg-type]
        kwargs["removal_at"],  # type: ignore[arg-type]
        coin_bars,
        daily([100.0] * 80) if btc is None else btc,
        taker_fee=FEE,
        tiers=TIERS,
        volume_window=30,
    )


def test_a_coins_path_is_measured_from_the_right_bars() -> None:
    path = measure(daily(crash_then_rebound()))
    assert isinstance(path, CoinPath)
    assert path.announced == ANNOUNCED.date()
    assert path.pre == 0.0  # flat before the announcement
    assert math.isclose(path.announcement_day or 0, math.log(0.5))  # close -1 to close 0
    # Entry at day 1's open (day 0's close, 0.5); exit at day R's open (day 69's close, 0.6).
    assert math.isclose(path.window, math.log(0.6 / 0.5))
    assert math.isclose(path.window_vs_btc, path.window)  # BTC flat
    assert math.isclose(path.round_trip, 2 * (FEE + 15e-4))  # a small coin's band
    assert path.flag is None


def test_the_window_is_judged_against_btc_and_the_band_uses_volume_known_then() -> None:
    btc = daily([100.0] * (DAY0 + 1) + [110.0] * (80 - DAY0 - 1))  # BTC +10% from day 61 on
    rising = daily(crash_then_rebound(), volume=600e6)  # the top volume band
    path = measure(rising, btc)
    assert isinstance(path, CoinPath)
    assert math.isclose(path.window_vs_btc, math.log(0.6 / 0.5) - math.log(1.1))
    assert math.isclose(path.round_trip, 2 * (FEE + 1e-4))


def test_bars_that_stop_early_exit_at_the_last_close_and_are_flagged() -> None:
    cut = daily(crash_then_rebound()[:66])  # last bar day 65
    path = measure(cut)
    assert isinstance(path, CoinPath)
    assert path.flag is not None
    assert "2024-03-06" in path.flag
    assert math.isclose(path.window, math.log(crash_then_rebound()[65] / 0.5))


def test_unmeasurable_coins_are_reported() -> None:
    assert "no USDT daily bars" in str(measure(daily([1.0])[:0]))
    midnight = START + timedelta(days=REMOVAL)
    assert "no tradable open" in str(measure(daily([1.0] * 80), removal_at=midnight))
    soon = START + timedelta(days=DAY0 + 1, hours=3)
    assert "no day 1" in str(measure(daily([1.0] * 80), removal_at=soon))
    assert "day 0 or day 1" in str(measure(daily([1.0] * 50)))
    assert "BTCUSDT" in str(measure(daily([1.0] * 80), daily([100.0] * 40)))


def path(article: str, window: float, day: int = 0, coin: str = "X") -> CoinPath:
    when = (START + timedelta(days=day)).date()
    return CoinPath(coin, article, when, when, -0.1, -0.2, window, -0.1, -0.2, window, 0.0045)


def test_coins_announced_together_count_as_one_event() -> None:
    found = events([path("a", 0.10, coin="A"), path("a", 0.30, coin="B"), path("b", -0.2, 5)])
    assert [e.coins for e in found] == [("A", "B"), ("X",)]
    assert math.isclose(found[0].window, 0.20)


def test_the_verdict_needs_double_costs_and_confidence() -> None:
    def verdict(windows: list[float]) -> str:
        paths = [path(f"e{i}", w, i) for i, w in enumerate(windows)]
        return run_delisting_study(paths, [], resamples=500).verdict

    assert verdict([0.05, 0.04, 0.06, 0.05, 0.03, 0.07]) == "REBOUND"
    assert verdict([-0.05, -0.04, -0.06, -0.05, -0.03, -0.07]) == "EXIT RULE"
    assert verdict([0.05, -0.05, 0.04, -0.04, 0.0, 0.01]) == "KILL"
    assert verdict([0.008] * 6) == "KILL"  # positive, but not twice the 0.45% round trip
    assert verdict([-0.008] * 6) == "KILL"  # negative, but within twice the cost


def test_share_above_zero_is_a_bootstrap_of_the_mean() -> None:
    assert share_above_zero([1.0, 2.0], 200, 0) == 1.0
    assert share_above_zero([-1.0, -2.0], 200, 0) == 0.0
    assert 0.3 < share_above_zero([1.0, -1.0, 1.0, -1.0], 2000, 0) < 0.7


def test_the_report_shows_events_figures_and_problems() -> None:
    flagged = CoinPath(
        "F", "a", START.date(), START.date(), None, None, 0.1, None, None, 0.1, 0.0045, "stop"
    )
    result = run_delisting_study([flagged, path("b", 0.05, 3)], ["Z: no USDT daily bars"])
    text = format_delisting_study(result)
    assert "2 coins in 2 announcements; 1 coins not measured" in text
    assert "Verdict:" in text
    assert "F: stop" in text
    assert "Z: no USDT daily bars" in text
    assert "n/a" in text  # a coin without a pre-drift shows n/a, not zero


def freeze_note(root: Path, implementation: str = "study:delistings") -> str:
    store = ResearchStore(root)
    slug = store.create("Delisting test").slug
    store.edit(slug, 1, dict.fromkeys(SECTIONS, "x"), implementation)
    store.freeze(slug, 1)
    return slug


def test_the_command_runs_a_frozen_note_once_before_the_holdout(tmp_path: Path) -> None:
    data, research = tmp_path / "data", tmp_path / "research"
    store = ParquetStore(data)
    store.write("ABCUSDT", Interval.D1, daily(crash_then_rebound()))
    store.write("BTCUSDT", Interval.D1, daily([100.0] * 80))
    late = datetime(2025, 10, 29, 3, tzinfo=UTC)
    delistings.save(
        pl.DataFrame(
            [
                ("ABC", ANNOUNCED, REMOVED, "a1", None, "t"),
                ("SUSD", ANNOUNCED, REMOVED, "a1", None, "t"),  # a stablecoin
                ("NOPE", ANNOUNCED, REMOVED, "a1", None, "t"),  # never had a USDT market
                ("LATE", late, late + timedelta(days=14), "a2", None, "t"),  # holdout
            ],
            schema=delistings.SCHEMA,
            orient="row",
        ),
        data,
    )
    slug = freeze_note(research)
    args = ["delisting-study", "--research", slug, "--research-dir", str(research)]
    args += ["--data-dir", str(data), "--config", REPO_CONFIG]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "1 coins in 1 announcements; 2 coins not measured" in result.output
    assert "SUSD: a stablecoin" in result.output
    assert "NOPE: no USDT daily bars" in result.output
    assert "LATE" not in result.output
    assert ResearchStore(research).get(slug).status == "backtested"


def test_the_command_refuses_a_note_for_another_implementation(tmp_path: Path) -> None:
    slug = freeze_note(tmp_path, "study:attention")
    args = ["delisting-study", "--research", slug, "--research-dir", str(tmp_path)]
    result = CliRunner().invoke(app, [*args, "--config", REPO_CONFIG])
    assert result.exit_code == 1
    assert "not study:delistings" in result.output


def test_the_short_side_is_the_negated_fall_not_its_log_inverse() -> None:
    fall = math.log(0.6)  # every event falls 40%
    result = run_delisting_study([path(f"e{i}", fall, i) for i in range(3)], [], resamples=50)
    assert "information only (long-only can't take it): +40.00% before borrowing" in (
        format_delisting_study(result)
    )
