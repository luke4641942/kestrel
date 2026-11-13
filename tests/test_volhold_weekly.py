"""Volatility-managed holding and one-week momentum, on hand-built daily closes (2024-01-01
is a Monday). Synthetic bars only: the strategies must not be run on market data until
their research notes are frozen."""

from __future__ import annotations

import math
import random
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from statistics import stdev

import polars as pl
import pytest

from kestrel.backtest.runs import RunRequest, execute
from kestrel.config import load_config
from kestrel.data.binance_vision import Month
from kestrel.data.features import attach_features
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategies.trend import TrendVolTarget
from kestrel.strategies.volhold import VolManagedHold
from kestrel.strategies.weekly import BtcLedWeekly, WeeklyMomentum
from kestrel.strategy import STRATEGIES, make_strategy, validated_weights
from tests.backtest.helpers import T0, bars

DAY = timedelta(days=1)
REPO_CONFIG = Path(__file__).resolve().parents[1] / "kestrel.toml"


def daily(log_returns: list[float], step: timedelta = DAY):  # type: ignore[no-untyped-def]
    closes, price = [100.0], 100.0
    for r in log_returns:
        price *= math.exp(r)
        closes.append(price)
    return bars([closes[0], *closes[:-1]], closes=closes, step=step)


# ---------- volatility-managed holding ----------


def vol_weights(frame, **params: int) -> list[float]:  # type: ignore[no-untyped-def]
    return validated_weights(VolManagedHold(**params), frame).to_list()


def test_fully_in_when_calm_and_scaled_down_when_wild() -> None:
    returns = [0.05, -0.03] * 40
    w = vol_weights(daily(returns))
    assert w[:30] == [0.0] * 30  # cash until 30 returns exist
    vol = stdev(returns[-30:]) * math.sqrt(365)
    assert w[-1] == pytest.approx(0.5 / vol)
    assert vol_weights(daily([0.001] * 40))[-1] == 1.0  # zero volatility: capped at 1


def test_it_is_trend_vol_target_with_every_trend_vote_up() -> None:
    # On a rising path every trend vote is 1, so both size identically.
    returns = [0.03 if i % 2 else -0.02 for i in range(300)]
    frame = daily(returns)
    held = vol_weights(frame)
    trend = TrendVolTarget().target_weights(frame).to_list()
    assert held[150:] == pytest.approx(trend[150:])


def test_volatility_days_scale_with_bar_size() -> None:
    w = vol_weights(daily([0.002, -0.001] * 120, step=timedelta(hours=4)))
    assert w[179] == 0.0  # 30 days of 4h bars is 180 returns
    assert w[180] > 0
    assert vol_weights(daily([])) == [0.0]


# ---------- one-week momentum ----------


def weekly(frame) -> list[float]:  # type: ignore[no-untyped-def]
    return validated_weights(WeeklyMomentum(), frame).to_list()


def test_holds_the_week_after_a_rising_week() -> None:
    # Day 6 (7 Jan) is the first Sunday, but has no close 7 days earlier: cash.
    up = weekly(daily([0.01] * 20))
    assert up[:13] == [0.0] * 13
    assert up[13:20] == [1.0] * 7  # decided at day 13's close (14 Jan), held through day 19
    down = weekly(daily([-0.01] * 20))
    assert sum(down) == 0


def test_a_week_is_seven_days_by_the_clock() -> None:
    frame = daily([0.01] * 6 + [-0.2] + [0.01] * 13)  # day 7 drops 20%
    w = weekly(frame)
    # Sunday day 13: close vs day 6's close spans the drop: down, so cash.
    assert w[13] == 0.0
    # A missing bar: the week's start is looked up by date, and a missing start is no signal.
    gappy = daily([0.01] * 20).filter(frame["open_time"] != frame["open_time"][6])
    assert weekly(gappy)[12] == 0.0  # Sunday day 13 has no close on day 6


def test_weekly_needs_daily_bars() -> None:
    with pytest.raises(ValueError, match="daily bars"):
        WeeklyMomentum().target_weights(daily([0.01] * 20, step=timedelta(hours=4)))
    assert weekly(daily([])) == [0.0]


@pytest.mark.parametrize("key", ["vol-hold", "weekly-momentum"])
def test_no_lookahead_on_a_random_walk(key: str) -> None:
    rnd = random.Random(4)
    frame = daily([rnd.gauss(0.0005, 0.03) for _ in range(800)])
    w = validated_weights(make_strategy(key, "BTCUSDT"), frame)  # type: ignore[arg-type]
    assert 0 < w.sum() < len(w)


def test_registered_as_research() -> None:
    for key in ("vol-hold", "weekly-momentum"):
        assert (STRATEGIES[key].kind, STRATEGIES[key].universe) == ("research", False)


# ---------- trading a coin on BTC's week ----------


def with_btc(coin_returns: list[float], btc_returns: list[float]):  # type: ignore[no-untyped-def]
    btc = daily(btc_returns)["close"]
    return daily(coin_returns).with_columns(btc_close=btc)


def led(frame) -> list[float]:  # type: ignore[no-untyped-def]
    return validated_weights(BtcLedWeekly(), frame).to_list()


def test_follows_btcs_week_not_its_own() -> None:
    # The coin falls every day, but BTC rises: hold the coin after BTC's rising week.
    w = led(with_btc([-0.01] * 20, [0.01] * 20))
    assert w[13:20] == [1.0] * 7
    assert sum(led(with_btc([0.01] * 20, [-0.01] * 20))) == 0


def test_a_missing_btc_close_means_no_signal() -> None:
    frame = with_btc([0.01] * 20, [0.01] * 20)
    gappy = frame.with_columns(
        btc_close=pl.when(pl.int_range(pl.len()) == 6).then(None).otherwise(pl.col("btc_close"))
    )
    assert led(gappy)[13] == 0.0  # Sunday day 13's week starts at day 6, with no BTC close


def test_btc_led_needs_the_feature_and_daily_bars() -> None:
    with pytest.raises(ValueError, match="needs the btc_close feature"):
        BtcLedWeekly().target_weights(daily([0.01] * 20))
    four_hourly = daily([0.01] * 20, step=timedelta(hours=4)).with_columns(
        btc_close=pl.col("close")
    )
    with pytest.raises(ValueError, match="daily bars"):
        BtcLedWeekly().target_weights(four_hourly)


def test_btc_led_has_no_lookahead() -> None:
    rnd = random.Random(8)
    frame = with_btc(
        [rnd.gauss(0.0005, 0.04) for _ in range(800)], [rnd.gauss(0.0005, 0.03) for _ in range(800)]
    )
    w = validated_weights(make_strategy("btc-led-weekly", "ETHUSDT"), frame)  # type: ignore[arg-type]
    assert 0 < w.sum() < len(w)
    assert STRATEGIES["btc-led-weekly"].features == ("btc_close",)


def test_the_feature_is_btcs_close_on_the_same_bar(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    btc = daily([0.01] * 9)  # 10 bars from 2024-01-01
    store.write("BTCUSDT", Interval.D1, btc.filter(pl.int_range(pl.len()) != 4))  # day 4 missing
    eth = daily([0.0] * 9)
    out = attach_features(eth, ("btc_close",), "ETHUSDT", Interval.D1, tmp_path)
    expected = btc["close"].to_list()
    expected[4] = None
    assert out["btc_close"].to_list() == expected
    assert attach_features(eth.clear(), ("btc_close",), "ETHUSDT", Interval.D1, tmp_path).is_empty()
    with pytest.raises(ValueError, match="no stored BTCUSDT 1d bars"):
        attach_features(eth, ("btc_close",), "ETHUSDT", Interval.D1, tmp_path / "empty")


def test_a_backtest_attaches_btc_itself(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.D1, daily([0.01] * 59))
    store.write("ETHUSDT", Interval.D1, daily([-0.005] * 59))
    request = RunRequest(
        "btc-led-weekly", "ETHUSDT", Interval.D1, Month(2024, 1), Month(2024, 2), Decimal(1000)
    )
    outcome = execute(request, store, load_config(REPO_CONFIG))
    assert outcome.result.fills[0].fill.side.value == "buy"  # ETH bought on BTC's rising week
    assert outcome.result.fills[0].time == T0 + 14 * DAY  # after Sunday 14 Jan's close
