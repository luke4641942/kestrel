"""The regime HMM: the forward pass against brute force, fitting on known data, labels,
and no lookahead through the monthly refits."""

from __future__ import annotations

import itertools
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel import regimes as rg
from kestrel.cli import app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.indicators import realised_volatility
from tests.backtest.helpers import T0, bars

DAY = timedelta(days=1)
REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")


def model(k: int = 2, d: int = 1, seed: int = 1) -> rg.Hmm:
    rnd = np.random.default_rng(seed)
    trans = rnd.uniform(0.2, 1, (k, k))
    start = rnd.uniform(0.2, 1, k)
    return rg.Hmm(
        start / start.sum(),
        trans / trans.sum(axis=1, keepdims=True),
        rnd.normal(0, 1, (k, d)),
        rnd.uniform(0.5, 2, (k, d)),
    )


def density(m: rg.Hmm, state: int, row: np.ndarray) -> float:
    mu, var = m.means[state], m.variances[state]
    return float(np.prod(np.exp(-((row - mu) ** 2) / (2 * var)) / np.sqrt(2 * np.pi * var)))


def test_the_forward_pass_matches_enumerating_every_path() -> None:
    m = model(k=2, d=2)
    x = np.random.default_rng(2).normal(0, 1, (5, 2))
    probs, log_l = rg.filtered(m, x)

    def joint(path: tuple[int, ...]) -> float:
        p = m.start[path[0]] * density(m, path[0], x[0])
        for t in range(1, len(path)):
            p *= m.trans[path[t - 1], path[t]] * density(m, path[t], x[t])
        return p

    for t in range(len(x)):
        paths = list(itertools.product(range(2), repeat=t + 1))
        total = sum(joint(p) for p in paths)
        for s in range(2):
            # Only inputs up to t: the filtered probability, not the smoothed one.
            expected = sum(joint(p) for p in paths if p[-1] == s) / total
            assert probs[t, s] == pytest.approx(expected)
    everything = sum(joint(p) for p in itertools.product(range(2), repeat=len(x)))
    assert log_l == pytest.approx(math.log(everything))


def simulate(n: int, seed: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Two sticky states: calm (mean 0.5, sd 0.5) and wild (mean -1, sd 2)."""
    rnd = np.random.default_rng(seed)
    trans = np.array([[0.97, 0.03], [0.05, 0.95]])
    states = [0]
    for _ in range(n - 1):
        states.append(int(rnd.random() > trans[states[-1], 0]))
    s = np.array(states)
    x = np.where(s == 0, rnd.normal(0.5, 0.5, n), rnd.normal(-1, 2, n))
    # Second input: the volatility proxy the states are sorted by.
    vol = np.where(s == 0, rnd.normal(0, 0.3, n), rnd.normal(2, 0.3, n))
    return np.column_stack([x, vol]), s


def test_fitting_recovers_known_states() -> None:
    x, truth = simulate(3000)
    m = rg.fit(x, 2, restarts=3, seed=0)
    assert m.means[:, 0] == pytest.approx([0.5, -1], abs=0.15)
    assert np.sqrt(m.variances[:, 0]) == pytest.approx([0.5, 2], abs=0.15)
    assert np.diag(m.trans) == pytest.approx([0.97, 0.95], abs=0.02)
    probs, _ = rg.filtered(m, x)
    assert (probs.argmax(axis=1) == truth).mean() > 0.95
    assert m.expected_days() == pytest.approx(1 / (1 - np.diag(m.trans)))


def test_em_never_lowers_the_likelihood() -> None:
    x, _ = simulate(500)
    _, history = rg._em(rg._initial(x, 3, np.random.default_rng(0)), x, 50, 0.0)
    assert all(b >= a - 1e-6 for a, b in itertools.pairwise(history))


def test_states_are_sorted_calmest_first_and_fits_repeat() -> None:
    m = model(k=3, d=2)
    m = rg.Hmm(m.start, m.trans, np.array([[0, 3.0], [0, 1.0], [0, 2.0]]), m.variances)
    r = rg.relabel(m)
    assert r.means[:, 1].tolist() == [1.0, 2.0, 3.0]
    order = [1, 2, 0]
    assert r.trans == pytest.approx(m.trans[np.ix_(order, order)])
    assert r.start == pytest.approx(m.start[order])
    x, _ = simulate(400)
    assert rg.fit(x, 2, restarts=2, seed=5).means == pytest.approx(
        rg.fit(x, 2, restarts=2, seed=5).means
    )
    with pytest.raises(ValueError, match="need more than"):
        rg.fit(x[:10], 2)


def regime_bars(n: int) -> pl.DataFrame:
    """Daily bars whose returns switch between a calm and a wild spell every ~60 days."""
    rnd = np.random.default_rng(7)
    closes, price = [], 100.0
    for i in range(n):
        wild = (i // 60) % 3 == 2
        price *= math.exp(rnd.normal(-0.004 if wild else 0.001, 0.05 if wild else 0.015))
        closes.append(price)
    return bars([closes[0], *closes[:-1]], closes=closes, step=DAY)


def test_inputs_are_the_log_return_and_log_volatility() -> None:
    frame = regime_bars(60)
    got = rg.inputs(frame, vol_days=20)
    c = frame["close"].to_list()
    assert got["return"][5] == pytest.approx(math.log(c[5] / c[4]))
    assert got["log_volatility"][30] == pytest.approx(math.log(realised_volatility(frame, 20)[30]))
    assert got["log_volatility"][19] is None  # still filling
    flat = bars([100.0] * 40, step=DAY)
    assert rg.inputs(flat)["log_volatility"].drop_nulls().is_empty()  # no log of zero
    with pytest.raises(ValueError, match="daily bars"):
        rg.inputs(bars([100.0] * 40, step=timedelta(hours=4)))
    assert rg.inputs(frame.head(1))["return"].to_list() == [None]


FAST = rg.Preset(states=2, vol_days=20, train_days=365, restarts=2, seed=0)


def test_walking_forward_refits_monthly_after_enough_history() -> None:
    frame = regime_bars(560)
    r = rg.walk_forward(frame, FAST)
    first = r.refits[0].month
    assert first.day == 1
    # Inputs start on day 20 (21 Jan 2024). 1 Dec 2024 would have 315 days of them, under 90%
    # of the 365-day window; 1 Jan 2025 has 345.
    assert first == datetime(2025, 1, 1, tzinfo=UTC)
    assert datetime(2024, 1, 1, tzinfo=UTC) == T0
    assert [x.month.month for x in r.refits[:3]] == [
        (first.month - 1 + i) % 12 + 1 for i in range(3)
    ]
    before = [p for t, p in zip(r.open_time, r.probabilities, strict=True) if t < first]
    assert set(map(str, before)) == {"None"}
    after = [p for t, p in zip(r.open_time, r.probabilities, strict=True) if t >= first]
    assert all(p is not None and sum(p) == pytest.approx(1) for p in after)
    assert math.isfinite(r.oos_log_likelihood)
    assert r.oos_log_likelihood == pytest.approx(sum(x.oos_log_likelihood for x in r.refits))


def test_no_probability_changes_when_later_days_are_added() -> None:
    frame = regime_bars(560)
    full = rg.walk_forward(frame, FAST).probabilities
    for cut in (430, 470, 500):  # mid-month and around month starts
        early = rg.walk_forward(frame.head(cut), FAST).probabilities
        for i, (a, b) in enumerate(zip(early, full[:cut], strict=True)):
            assert (a is None) == (b is None), f"day {i} with history cut at {cut}"
            if a is not None and b is not None:
                assert a == pytest.approx(b), f"day {i} changes when days after {cut} are added"


def test_describing_the_states() -> None:
    frame = bars([100.0] * 8, closes=[100, 101, 102, 100, 98, 99, 100, 101], step=DAY)
    m = rg.Hmm(
        np.array([0.5, 0.5]),
        np.array([[0.9, 0.1], [0.2, 0.8]]),
        np.zeros((2, 2)),
        np.ones((2, 2)),
    )
    probs = [None, [0.9, 0.1], [0.8, 0.2], [0.1, 0.9], [0.3, 0.7], [0.6, 0.4], [0.7, 0.3], None]
    times = frame["open_time"].to_list()
    regimes = rg.Regimes(times, probs, [rg.Refit(times[1], m, 0.0)])
    calm, wild = rg.describe(regimes, frame)
    assert (calm.share, wild.share) == (4 / 6, 2 / 6)
    assert calm.average_run == 2  # days 1-2 and 5-6
    assert wild.average_run == 2  # days 3-4
    assert wild.mean_return == pytest.approx((math.log(100 / 102) + math.log(98 / 100)) / 2)
    assert calm.expected_days == pytest.approx(10)
    assert wild.expected_days == pytest.approx(5)
    assert rg.describe(rg.Regimes(times, [None] * 8, []), frame) == []


def test_the_report() -> None:
    frame = regime_bars(560)
    r = rg.walk_forward(frame, FAST)
    text = rg.format_regimes(r, frame, "BTCUSDT", FAST)
    assert f"{len(r.refits)} monthly refits from 2025-01, each on the 365 days before it" in text
    assert "Switching odds from the" in text
    assert "never by trading results" in text
    empty = rg.Regimes(r.open_time, [None] * len(r.open_time), [])
    assert "not enough history" in rg.format_regimes(empty, frame, "BTCUSDT")


def test_the_cli(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", Interval.D1, regime_bars(200))
    base = ["regimes", "BTCUSDT", "--data-dir", str(tmp_path), "--config", REPO_CONFIG]
    ok = CliRunner().invoke(app, [*base, "--start", "2024-01", "--end", "2024-12"])
    assert ok.exit_code == 0, ok.output
    assert "not enough history for a first fit" in ok.output  # 200 days, 4 years needed
    holdout = CliRunner().invoke(app, [*base, "--start", "2024-01", "--end", "2025-11"])
    assert holdout.exit_code == 1
    assert "studies never use it" in holdout.output
