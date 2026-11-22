"""What a strategy is waiting for (kestrel.strategies.conditions): each test's "move needed"
flips it exactly at the next close, and the tests agree with the strategies' own decisions
on random histories, so the Trading page can't drift from the rules."""

from __future__ import annotations

import math
import random
from datetime import timedelta

import polars as pl
import pytest

from kestrel.data.features import BTC_CLOSE
from kestrel.strategies.btc_regime import BtcRegimeTrend
from kestrel.strategies.btc_rotation import LARGE_8, BtcRegimeRotation
from kestrel.strategies.conditions import above_average, above_past
from kestrel.strategies.trend import TrendVolTarget
from tests.backtest.helpers import bars
from tests.backtest.test_portfolio import panel
from tests.test_btc_rotation import MONDAY, walk

DAY = timedelta(days=1)


def prices(seed: int, n: int = 400, drift: float = 0.0, vol: float = 0.03) -> list[float]:
    rnd = random.Random(seed)
    p, out = 100.0, []
    for _ in range(n):
        p *= math.exp(drift + rnd.gauss(0, vol))
        out.append(p)
    return out


@pytest.mark.parametrize("margin", [0.0, 0.03])
def test_the_move_needed_flips_an_average_test_at_the_next_close(margin: float) -> None:
    for seed in range(20):
        closes = prices(seed, 80)
        c = above_average("x", "X", closes, 50, margin)
        assert c is not None
        assert c.met == (closes[-1] > c.level)
        for nudge, met in ((1.0001, True), (0.9999, False)):
            after = above_average(
                "x", "X", [*closes, closes[-1] * (1 + c.move) * nudge], 50, margin
            )
            assert after is not None
            assert after.met is met
    assert above_average("x", "X", [1.0] * 10, 50) is None


def test_the_move_needed_flips_a_past_close_test_at_the_next_close() -> None:
    for seed in range(20):
        closes = prices(seed, 60)
        c = above_past("x", "X", closes, 30)
        assert c is not None
        assert c.level == closes[-31]
        for nudge, met in ((1.0001, True), (0.9999, False)):
            after = above_past("x", "X", [*closes, closes[-1] * (1 + c.move) * nudge], 30)
            assert after is not None
            assert after.met is met
    assert above_past("x", "X", [1.0] * 30, 30) is None


def test_btc_regime_trend_is_in_the_market_exactly_when_both_tests_hold() -> None:
    for seed in range(5):
        coin, btc = prices(seed, 300, vol=0.04), prices(seed + 100, 300, vol=0.03)
        b = bars([coin[0], *coin[:-1]], closes=coin, step=DAY).with_columns(
            pl.Series(BTC_CLOSE, btc)
        )
        s = BtcRegimeTrend("ETHUSDT")
        weights = s.target_weights(b).to_list()
        for t in range(60, 300, 7):
            w = s.waiting(b.head(t))
            assert w is not None
            assert w.rule == "all"
            assert [c.subject for c in w.conditions] == ["BTCUSDT", "ETHUSDT"]
            assert all(c.met for c in w.conditions) == (weights[t - 1] > 0)


def test_trend_vol_target_holds_a_third_for_each_trend_that_is_up() -> None:
    for seed in range(5):
        closes = prices(seed, 400, vol=0.04)
        b = bars([closes[0], *closes[:-1]], closes=closes, step=DAY)
        s = TrendVolTarget("BTCUSDT")
        votes = s.vote_count(b).to_list()
        for t in range(130, 400, 9):
            w = s.waiting(b.head(t))
            assert w is not None
            assert w.rule == "any"
            assert sum(c.met for c in w.conditions) == votes[t - 1]
    four_hourly = bars([1.0] * 900, step=timedelta(hours=4))
    assert TrendVolTarget("BTCUSDT").waiting(four_hourly) is None  # daily only


def test_g1_holds_on_a_sunday_exactly_when_its_btc_test_holds_and_names_its_picks() -> None:
    for seed in range(3):
        p = panel(
            {s: walk(0.001 * (k % 3), seed * 10 + k, vol=0.03) for k, s in enumerate(LARGE_8)},
            start=MONDAY,
        )
        s = BtcRegimeRotation()
        out = s.target_weights(p)
        sundays = sorted(out["open_time"].unique().to_list())
        checked = 0
        for day in sundays[-30:]:
            held = out.filter((pl.col("open_time") == day) & pl.col("symbol").is_not_null())
            w = s.waiting(p.filter(pl.col("open_time") <= day))
            assert w is not None
            assert w.decides == "Sunday closes"
            (test,) = w.conditions
            # Met with no coin old enough to rank holds nothing; would_hold says so.
            assert (test.met and bool(w.would_hold)) == (held.height > 0)
            if held.height:
                assert sorted(w.would_hold) == held["symbol"].to_list()  # ranked vs by name
                checked += 1
        assert checked  # some Sundays were in the market
