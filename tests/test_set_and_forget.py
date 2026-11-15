"""Set and Forget's mechanical pieces (higher timeframes, swings, bias, zones, engulfing)
and the whole strategy's levels and lack of lookahead."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from kestrel.strategies.set_and_forget import (
    STOP_ATR,
    SetAndForget,
    Swing,
    engulfing,
    resample,
    rising,
    swings,
    zones,
)
from kestrel.strategy import make_strategy, validated_brackets
from tests.backtest.helpers import bars

HOUR = timedelta(hours=1)
WED = datetime(2024, 1, 3, tzinfo=UTC)  # a Wednesday


def hourly(closes: list[float], start: datetime = WED) -> pl.DataFrame:
    opens = [closes[0], *closes[:-1]]
    return bars(opens, closes=closes, start=start, step=HOUR)


def test_higher_timeframes_close_on_utc_boundaries_and_weeks_start_on_monday() -> None:
    frame = hourly([float(i) for i in range(24 * 10)])
    four = resample(frame, "4h")
    assert four["open_time"][0] == WED
    assert four["end"][0] == WED + 4 * HOUR
    assert four["high"][0] == 3.0  # hours 0-3
    daily = resample(frame, "1d")
    assert daily["close"][0] == 23.0
    weekly = resample(frame, "1w")
    assert weekly["open_time"][0] == datetime(2024, 1, 1, tzinfo=UTC)  # the Monday before
    assert weekly["end"][0] == datetime(2024, 1, 8, tzinfo=UTC)


def test_a_swing_is_known_only_when_its_confirming_bar_closes() -> None:
    lows = [5.0, 4.0, 3.0, 4.0, 5.0, 6.0]
    frame = pl.DataFrame(
        {
            "open_time": [WED + i * HOUR for i in range(6)],
            "high": [x + 1 for x in lows],
            "low": lows,
        }
    ).with_columns(end=pl.col("open_time") + HOUR)
    highs, found = swings(frame, 2)
    assert highs == []
    (low,) = found
    assert low.price == 3.0
    assert low.at == WED + 2 * HOUR
    assert low.known_at == WED + 5 * HOUR  # bar 4 (two after) closes at 05:00


def test_bias_needs_the_last_two_known_swings_rising() -> None:
    up = [Swing(WED, WED, 1.0), Swing(WED + HOUR, WED, 2.0), Swing(WED + 5 * HOUR, WED, 1.5)]
    known = [s.known_at for s in up]
    assert rising(up, known, WED + 2 * HOUR)  # 1 then 2
    assert not rising(up, known, WED + 5 * HOUR)  # 2 then 1.5
    assert not rising(up, known, WED)  # only one known


def test_zones_are_bands_holding_enough_lows() -> None:
    lows = [100.0, 100.4, 100.8, 103.0, 110.0, 110.2]
    assert zones(lows, width=1.0, touches=3) == [(100.0, 100.8)]
    assert (110.0, 110.2) in zones(lows, width=1.0, touches=2)
    assert zones(lows, width=0.3, touches=3) == []


def test_engulfing_needs_a_falling_bar_then_a_rise_through_its_open() -> None:
    o, c = [10.0, 9.0], [9.0, 10.5]
    assert engulfing(o, c, 1)
    assert not engulfing([10.0, 9.2], [9.0, 10.5], 1)  # opened above the prior close
    assert not engulfing([10.0, 9.0], [9.0, 9.9], 1)  # closed below the prior open
    assert not engulfing(o, c, 0)


def drifting(seed: int = 0, days: int = 400) -> pl.DataFrame:
    rnd = random.Random(seed)
    price, closes = 100.0, []
    for _ in range(24 * days):
        price *= math.exp(0.00025 + 0.004 * rnd.gauss(0, 1))
        closes.append(price)
    opens = [closes[0], *closes[:-1]]
    return bars(
        opens,
        closes=closes,
        highs=[max(a, b) * 1.002 for a, b in zip(opens, closes, strict=True)],
        lows=[min(a, b) * 0.998 for a, b in zip(opens, closes, strict=True)],
        start=datetime(2024, 1, 1, tzinfo=UTC),
        step=HOUR,
    )


def loose() -> SetAndForget:
    return SetAndForget("BTCUSDT", swing_bars=2, touches=2, zone_atr_tenths=10)


def test_entries_are_engulfing_bars_with_the_stop_below_and_the_target_at_target_r() -> None:
    frame = drifting()
    out = loose().brackets(frame)
    entries = frame.with_columns(out).with_row_index().filter(pl.col("enter"))
    assert entries.height > 0
    o, c = frame["open"].to_list(), frame["close"].to_list()
    for row in entries.iter_rows(named=True):
        t = row["index"]
        assert engulfing(o, c, t)
        assert row["stop"] < row["low"]  # below the zone the bar dipped into
        risk = row["close"] - row["stop"]
        assert row["target"] == pytest.approx(row["close"] + 3 * risk)


def test_no_lookahead_and_the_registry_builds_it() -> None:
    validated_brackets(loose(), drifting(), checkpoints=12)  # raises on lookahead
    built = make_strategy("set-and-forget", "ETHUSDT", {"touches": 4})
    assert isinstance(built, SetAndForget)
    assert built.touches == 4
    assert 0 < STOP_ATR < 1
