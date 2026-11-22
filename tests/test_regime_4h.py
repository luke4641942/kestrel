"""The 4h regime model (kestrel.regimes.FAST_PRESET), its store, the regime_volatile_4h
feature on daily bars, and trend-vol-target filtered by it."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel import regimes as rg
from kestrel.cli import app
from kestrel.data.features import REGIME_VOLATILE_4H, attach_features
from kestrel.data.schema import Interval
from kestrel.strategies.regime import TrendVolTargetRegime4h
from kestrel.strategies.trend import TrendVolTarget
from kestrel.strategy import STRATEGIES, make_strategy, validated_weights
from tests.backtest.helpers import T0, bars
from tests.test_volhold_weekly import daily

H4 = timedelta(hours=4)
SMALL = rg.Preset(states=2, vol_days=20, train_days=60, restarts=2, seed=0, bar_hours=4)
REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")


def four_hour_bars(n: int) -> pl.DataFrame:
    """4h bars from T0 (2024-01-01) whose returns switch between calm and wild spells."""
    rnd = np.random.default_rng(3)
    closes, price = [], 100.0
    for i in range(n):
        wild = (i // 90) % 3 == 2
        price *= math.exp(rnd.normal(0.0, 0.03 if wild else 0.008))
        closes.append(price)
    return bars([closes[0], *closes[:-1]], closes=closes, step=H4)


def test_the_fast_preset_is_findings_23s_4h_model() -> None:
    p = rg.FAST_PRESET
    assert (p.states, p.vol_days, p.train_days, p.restarts, p.seed) == (3, 20, 365, 3, 0)
    assert (p.bar, p.bars_per_day) == (H4, 6)
    assert rg.PRESET.bar == timedelta(days=1)


def test_a_4h_walk_forward_counts_its_window_in_4h_bars() -> None:
    frame = four_hour_bars(6 * 120)
    r = rg.walk_forward(frame, SMALL)
    # Inputs start at bar 20. 1 Feb has 31 days behind it, under 90% of the 60-day window;
    # 1 Mar has 60 days (340 of 360 bars).
    assert r.refits[0].month == datetime(2024, 3, 1, tzinfo=UTC)
    assert all(
        p is None
        for t, p in zip(r.open_time, r.probabilities, strict=True)
        if t < T0.replace(month=3)
    )
    with pytest.raises(ValueError, match="regimes need daily bars"):
        rg.walk_forward(frame)  # the daily preset refuses 4h bars
    with pytest.raises(ValueError, match="need 4:00:00 bars"):
        rg.walk_forward(daily([0.01] * 100), SMALL)


def test_no_4h_probability_changes_when_later_bars_are_added() -> None:
    frame = four_hour_bars(6 * 110)
    full = rg.walk_forward(frame, SMALL).probabilities
    for cut in (6 * 61, 6 * 61 + 3, 6 * 90):  # a month start, mid-day, mid-month
        early = rg.walk_forward(frame.head(cut), SMALL).probabilities
        for i, (a, b) in enumerate(zip(early, full[:cut], strict=True)):
            assert (a is None) == (b is None)
            if a is not None and b is not None:
                assert a == pytest.approx(b), f"bar {i} changes when bars after {cut} are added"


def test_a_4h_description_is_in_days() -> None:
    frame = four_hour_bars(6 * 120)
    r = rg.walk_forward(frame, SMALL)
    latest = r.refits[-1].model
    summary = rg.describe(r, frame)
    assert [s.expected_days for s in summary] == pytest.approx(list(latest.expected_days() / 6))


def store_4h(tmp_path: Path, frame: pl.DataFrame, states: list[int | None]) -> None:
    probs: list[list[float] | None] = [
        None if s is None else [0.8 if k == s else 0.1 for k in range(3)] for s in states
    ]
    model = rg.Hmm(np.full(3, 1 / 3), np.full((3, 3), 1 / 3), np.zeros((3, 2)), np.ones((3, 2)))
    times = frame["open_time"].to_list()
    fit = rg.Regimes(times, probs, [rg.Refit(T0, model, 0.0)], rg.FAST_PRESET)
    rg.RegimeStore(tmp_path, "4h").save("BTCUSDT", fit, frame)


def test_the_4h_store_is_apart_from_the_daily_one(tmp_path: Path) -> None:
    frame = four_hour_bars(18)
    store_4h(tmp_path, frame, [0] * 18)
    assert (tmp_path / "regimes" / "4h" / "BTCUSDT.json").exists()
    assert rg.RegimeStore(tmp_path).summaries() == []  # the Regimes page lists daily fits only
    assert rg.RegimeStore(tmp_path, "4h").load("BTCUSDT")["preset"]["bar_hours"] == 4
    with pytest.raises(ValueError, match="1d or 4h"):
        rg.RegimeStore(tmp_path, "1h")


def test_each_day_gets_the_state_of_its_last_4h_bar(tmp_path: Path) -> None:
    days = daily([0.01] * 2)  # 3 days from T0
    frame = four_hour_bars(18)  # the same 3 days in 4h bars
    #        day 1: 00 04 08 12 16 20    day 2               day 3
    states = [None, None, 2, 2, 2, 0, 0, 0, 0, 0, 0, 2, 1, 1, 2, 2, 2, 1]
    store_4h(tmp_path, frame, states)
    out = attach_features(days, ("regime_volatile_4h",), "btcusdt", Interval.D1, tmp_path)
    # Day 1's last bar (20:00) is calm though it was volatile earlier: only the bar that
    # closes with the day counts. Day 2's 20:00 bar is volatile.
    assert out[REGIME_VOLATILE_4H].to_list() == [0.0, 1.0, 0.0]
    # Known at the day's close: a fit ending with day 2 gives days 1-2 the same values.
    store_4h(tmp_path, frame.head(12), states[:12])
    early = attach_features(days.head(2), ("regime_volatile_4h",), "BTCUSDT", Interval.D1, tmp_path)
    assert early[REGIME_VOLATILE_4H].to_list() == [0.0, 1.0]
    # A fit that stops at 16:00 hasn't seen day 2's close: refused, not left blank.
    store_4h(tmp_path, frame.head(11), states[:11])
    with pytest.raises(ValueError, match="4h regime fit ends"):
        attach_features(days.head(2), ("regime_volatile_4h",), "BTCUSDT", Interval.D1, tmp_path)


def test_the_4h_feature_refuses_what_it_cant_cover(tmp_path: Path) -> None:
    days = daily([0.01] * 2)
    with pytest.raises(ValueError, match=r"no 4h regime fit for BTCUSDT.*--interval 4h --save"):
        attach_features(days, ("regime_volatile_4h",), "BTCUSDT", Interval.D1, tmp_path)
    with pytest.raises(ValueError, match="regime_volatile_4h goes on daily bars"):
        attach_features(days, ("regime_volatile_4h",), "BTCUSDT", Interval.H4, tmp_path)


def test_trend_vol_target_out_in_the_4h_volatile_regime() -> None:
    import random

    rnd = random.Random(4)
    frame = daily([rnd.gauss(0.002, 0.03) for _ in range(400)])
    flags = [None] * 150 + [float(rnd.random() < 0.3) for _ in range(401 - 150)]
    frame = frame.with_columns(pl.Series(REGIME_VOLATILE_4H, flags, dtype=pl.Float64))
    plain = TrendVolTarget().target_weights(frame).to_list()
    filtered = TrendVolTargetRegime4h().target_weights(frame).to_list()
    for p, f, v in zip(plain, filtered, flags, strict=True):
        assert f == (0.0 if v == 1.0 else p)
    assert any(v == 1.0 and p > 0 for p, v in zip(plain, flags, strict=True))
    assert TrendVolTargetRegime4h(regime_filter=0).target_weights(frame).to_list() == plain
    with pytest.raises(ValueError, match="needs the regime_volatile_4h feature"):
        TrendVolTargetRegime4h().target_weights(daily([0.01] * 30))
    spec = STRATEGIES["trend-vol-target-regime-4h"]
    assert (spec.kind, spec.features) == ("research", ("regime_volatile_4h",))
    w = validated_weights(make_strategy("trend-vol-target-regime-4h", "BTCUSDT"), frame)  # type: ignore[arg-type]
    assert 0 < w.sum() < len(w)


def test_the_cli_fits_4h_and_refuses_other_bars(tmp_path: Path) -> None:
    from kestrel.data.store import ParquetStore

    ParquetStore(tmp_path).write("BTCUSDT", Interval.H4, four_hour_bars(6 * 40))
    base = ["regimes", "BTCUSDT", "--data-dir", str(tmp_path), "--config", REPO_CONFIG]
    ok = CliRunner().invoke(
        app, [*base, "--interval", "4h", "--start", "2024-01", "--end", "2024-02"]
    )
    assert ok.exit_code == 0, ok.output
    assert "not enough history for a first fit" in ok.output  # 40 days; a year needed
    bad = CliRunner().invoke(app, [*base, "--interval", "1h"])
    assert bad.exit_code == 1
    assert "--interval is 1d or 4h" in bad.output
