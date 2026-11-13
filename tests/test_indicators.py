"""Indicators: hand-checked values, the textbook seeding, and no lookahead in any preset."""

from __future__ import annotations

import math
import random
from datetime import timedelta
from statistics import pstdev, stdev

import polars as pl
import pytest

from kestrel import indicators as ind
from kestrel.strategies.geometry import atr as geometry_atr
from tests.backtest.helpers import bars
from tests.chart import random_walk


def s(values: list[float]) -> pl.Series:
    return pl.Series(values, dtype=pl.Float64)


def test_sma_and_ema_are_seeded_and_null_until_ready() -> None:
    x = s([1, 2, 3, 4, 5])
    assert ind.sma(x, 3).to_list() == [None, None, 2, 3, 4]
    # EMA 3: seeded with the SMA of the first three (2), then alpha = 0.5.
    assert ind.ema(x, 3).to_list() == [None, None, 2, 3, 4]
    assert ind.ema(s([1, 2, 3, 10]), 3).to_list() == [None, None, 2, 6]


def test_rsi_extremes_and_a_hand_checked_value() -> None:
    assert ind.rsi(s([float(i) for i in range(20)]), 14)[-1] == 100
    assert ind.rsi(s([float(20 - i) for i in range(20)]), 14)[-1] == 0
    assert ind.rsi(s([5.0] * 20), 14)[-1] == 50
    # n = 2: changes +2, -1, then +1. Seed: gain 1.0, loss 0.5; then Wilder:
    # gain = 1.0 + (1 - 1.0) / 2 = 1.0, loss = 0.5 + (0 - 0.5) / 2 = 0.25 -> RS 4 -> RSI 80.
    r = ind.rsi(s([10, 12, 11, 12]), 2).to_list()
    assert r[:2] == [None, None]
    assert r[2] == pytest.approx(100 - 100 / (1 + 1.0 / 0.5))
    assert r[3] == pytest.approx(80)


def test_atr_is_wilder_and_the_one_geometry_uses() -> None:
    frame = random_walk(300)
    h, lo, c = frame["high"].to_list(), frame["low"].to_list(), frame["close"].to_list()
    # Reference, written out independently: seed with the mean of the first 14 true
    # ranges, then value = (value * 13 + tr) / 14.
    trs = [h[0] - lo[0]] + [
        max(h[t] - lo[t], abs(h[t] - c[t - 1]), abs(lo[t] - c[t - 1])) for t in range(1, 300)
    ]
    expected: list[float | None] = [None] * 13 + [sum(trs[:14]) / 14]
    for tr in trs[14:]:
        expected.append((expected[-1] * 13 + tr) / 14)  # type: ignore[operator]
    assert ind.atr(frame, 14).to_list() == pytest.approx(expected, nan_ok=True)
    assert geometry_atr(h, lo, c) == ind.atr(frame, 14).to_list()
    # A gap counts: the second bar's range is 1, but its high is 6 above the last close.
    assert ind.true_range([10, 16], [9, 15], [10, 15.5]) == [1, 6]


def test_macd_line_signal_and_histogram() -> None:
    close = random_walk(200)["close"]
    m = ind.macd(close)
    line = (ind.ema(close, 12) - ind.ema(close, 26)).to_list()
    assert m.line.to_list() == line
    first = next(i for i, v in enumerate(line) if v is not None)  # bar 25
    assert first == 25
    assert m.signal.to_list()[first + 7] is None
    assert m.signal[first + 8] == pytest.approx(sum(line[first : first + 9]) / 9)  # type: ignore[arg-type]
    assert m.histogram[-1] == pytest.approx(m.line[-1] - m.signal[-1])


def test_bollinger_bands_use_the_population_deviation() -> None:
    x = [float(v) for v in [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9, 3, 2, 3, 8, 4]]
    b = ind.bollinger(s(x), 20, 2)
    assert b.middle[-1] == pytest.approx(sum(x) / 20)
    assert b.upper[-1] - b.middle[-1] == pytest.approx(2 * pstdev(x))
    assert b.lower[-1] == pytest.approx(b.middle[-1] - 2 * pstdev(x))
    assert b.upper.to_list()[:19] == [None] * 19


def test_rate_of_change_and_distance_from_average() -> None:
    x = s([100, 110, 121, 100])
    assert ind.rate_of_change(x, 2).to_list()[2:] == pytest.approx([0.21, 100 / 110 - 1])
    assert ind.ma_distance(x, 2)[-1] == pytest.approx(100 / 110.5 - 1)


def test_volatility_is_annualised_from_days_on_any_bar_size() -> None:
    rets = [0.02, -0.01] * 60
    closes, p = [100.0], 100.0
    for r in rets:
        p *= math.exp(r)
        closes.append(p)
    daily = bars(closes, step=timedelta(days=1))
    v = ind.realised_volatility(daily, 30)
    assert v[29] is None
    assert v[-1] == pytest.approx(stdev(rets[-30:]) * math.sqrt(365))
    hourly4 = bars(closes, step=timedelta(hours=4))  # 30 days is 180 bars
    assert ind.realised_volatility(hourly4, 30)[-1] is None
    assert ind.realised_volatility(hourly4, 10)[-1] == pytest.approx(
        stdev(rets[-60:]) * math.sqrt(365 * 6)
    )
    with pytest.raises(ValueError, match="at least two bars"):
        ind.realised_volatility(daily.head(1), 30)


def test_relative_volume_compares_with_the_bars_before() -> None:
    v = s([10, 10, 10, 30])
    assert ind.relative_volume(v, 3).to_list() == [None, None, None, 3]


def test_on_balance_volume() -> None:
    obv = ind.on_balance_volume(s([10, 11, 11, 9, 12]), s([5, 2, 7, 3, 4]))
    assert obv.to_list() == [0, 2, 2, -1, 3]


def test_presets_compute_named_lines_and_refuse_unknown_keys() -> None:
    frame = random_walk(300)
    out = ind.compute_presets(frame, ["rsi14", "macd", "rsi14", "bollinger"])
    assert list(out) == ["rsi14", "macd", "bollinger"]
    assert set(out["macd"]) == {"macd", "signal", "histogram"}
    assert all(line.len() == frame.height for lines in out.values() for line in lines.values())
    every = ind.compute_presets(frame, list(ind.PRESETS))
    assert all(tuple(every[k]) == p.lines for k, p in ind.PRESETS.items())
    assert ind.PRESETS["rsi14"].guides == (30, 70)
    with pytest.raises(ValueError, match="unknown indicator"):
        ind.compute_presets(frame, ["rsi7"])


@pytest.mark.parametrize("key", list(ind.PRESETS))
def test_no_preset_uses_the_future(key: str) -> None:
    """Every value on a truncated history equals the same value on the full history."""
    rnd = random.Random(1)
    frame = random_walk(400).with_columns(
        volume=pl.Series([rnd.uniform(1, 100) for _ in range(400)])
    )
    full = ind.compute_presets(frame, [key])[key]
    for cut in (50, 150, 250, 399):
        early = ind.compute_presets(frame.head(cut), [key])[key]
        for name, line in early.items():
            assert line.to_list() == pytest.approx(full[name].head(cut).to_list(), nan_ok=True), (
                f"{key}.{name} changes before bar {cut} when later bars are added"
            )
