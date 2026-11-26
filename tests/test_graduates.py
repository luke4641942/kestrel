"""The exploration experiment's two graduates: their weights on hand-checkable bars, and
that neither looks ahead."""

from __future__ import annotations

from datetime import timedelta

import polars as pl
import pytest

from kestrel.data.features import BTC_CLOSE
from kestrel.strategies.btc_regime import BtcRegimeAblation, BtcRegimeTrend
from kestrel.strategies.crash_rebound import CrashRebound
from kestrel.strategy import make_strategy, validated_weights
from tests.backtest.helpers import bars

DAY = timedelta(days=1)


def daily(closes: list[float], btc: list[float | None] | None = None) -> pl.DataFrame:
    frame = bars(closes, step=DAY)
    return frame.with_columns(pl.Series(BTC_CLOSE, btc or [100.0] * len(closes), pl.Float64))


def test_the_regime_trend_needs_btc_above_its_average_and_the_coin_rising() -> None:
    n = 12
    rising = [100.0 * 1.01**i * (1.002 if i % 2 else 0.999) for i in range(n)]  # some volatility
    btc_up = [100.0 + i for i in range(n)]
    w = BtcRegimeTrend("X", btc_days=3, coin_days=2, vol_days=3, target_vol_pct=100)
    weights = w.target_weights(daily(rising, btc_up)).to_list()
    assert weights[:3] == [0.0] * 3  # volatility needs 3 returns: first known at row 3's close
    assert all(x > 0 for x in weights[4:])
    btc_down = [100.0 - i for i in range(n)]
    assert w.target_weights(daily(rising, btc_down)).sum() == 0  # BTC below its average
    falling = [100.0 * 0.99**i for i in range(n)]
    assert w.target_weights(daily(falling, btc_up)).sum() == 0  # the coin isn't rising
    missing = [None] * n
    assert w.target_weights(daily(rising, missing)).sum() == 0  # no BTC bar: cash


def test_the_btc_average_waits_for_btc_days_of_the_coins_own_history() -> None:
    """Findings 30: the frozen rule's BTC average runs over the coin's rows, so a new coin
    is in cash until its btc_days-th bar even when its trend and volatility are known."""
    n = 20
    rising = [100.0 * 1.01**i * (1.002 if i % 2 else 0.999) for i in range(n)]
    btc_up = [100.0 + i for i in range(n)]
    w = BtcRegimeTrend("X", btc_days=10, coin_days=2, vol_days=3, target_vol_pct=100)
    weights = w.target_weights(daily(rising, btc_up)).to_list()
    assert weights[:9] == [0.0] * 9  # trend and volatility known from row 3; BTC from row 9
    assert all(x > 0 for x in weights[9:])


def test_the_regime_trend_is_sized_to_the_volatility_target() -> None:
    n = 40
    wobble = [100.0 * (1.05 if i % 2 else 0.97) ** 1 * 1.004**i for i in range(n)]
    btc = [100.0 + i for i in range(n)]
    low = BtcRegimeTrend("X", btc_days=5, coin_days=5, vol_days=10, target_vol_pct=10)
    high = BtcRegimeTrend("X", btc_days=5, coin_days=5, vol_days=10, target_vol_pct=100)
    lw = low.target_weights(daily(wobble, btc)).to_list()
    hw = high.target_weights(daily(wobble, btc)).to_list()
    held = [i for i in range(n) if hw[i] > 0]
    assert held
    assert all(0 < lw[i] < hw[i] <= 1.0 for i in held)  # a lower target, a smaller weight


def test_a_crash_holds_the_coin_for_hold_days() -> None:
    closes = [100.0] * 8 + [65.0, 71.0, 72.0, 73.0, 74.0]  # -35% over 7 days at row 8 only
    w = CrashRebound("X", drop_pct=30, days=7, hold_days=3).target_weights(daily(closes))
    assert w.to_list() == [0.0] * 8 + [1.0, 1.0, 1.0, 0.0, 0.0]
    mild = [100.0] * 8 + [80.0] * 5  # -20%: not a crash
    assert CrashRebound("X").target_weights(daily(mild)).sum() == 0


@pytest.mark.parametrize("key", ["btc-regime-trend", "btc-regime-ablation", "crash-rebound"])
def test_no_lookahead_and_the_registry_builds_them(key: str) -> None:
    closes = [100.0 * (0.6 if i in (60, 61) else 1.0) * 1.003**i for i in range(150)]
    btc = [100.0 + (i % 40) for i in range(150)]
    strategy = make_strategy(key, "XUSDT")
    validated_weights(strategy, daily(closes, btc))  # raises on any lookahead


def test_the_ablation_with_every_part_on_is_the_regime_trend() -> None:
    closes = [100.0 * (0.6 if i in (60, 61) else 1.0) * 1.003**i for i in range(150)]
    btc = [100.0 + (i % 40) for i in range(150)]
    frame = daily(closes, btc)
    full = BtcRegimeTrend("X").target_weights(frame)
    assert BtcRegimeAblation("X").target_weights(frame).to_list() == full.to_list()

    def weights(**off: int) -> list[float]:
        return BtcRegimeAblation("X", **off).target_weights(frame).to_list()

    no_btc, no_trend, fully_in = weights(btc_filter=0), weights(coin_trend=0), weights(vol_sizing=0)
    vol_hold = weights(btc_filter=0, coin_trend=0)
    held = [i for i, w in enumerate(full.to_list()) if w > 0]
    assert all(no_btc[i] == full[i] for i in held)  # dropping a filter only adds days
    assert sum(x > 0 for x in no_btc) >= len(held)
    assert all(no_trend[i] == full[i] for i in held)
    assert all(fully_in[i] == 1.0 for i in held)  # same days in, but fully
    assert [i for i, w in enumerate(fully_in) if w > 0] == held
    start = next(i for i, w in enumerate(vol_hold) if w > 0)
    assert all(w > 0 for w in vol_hold[start:])  # vol-hold: always in once warmed up
    assert all(w == 0 for w in no_btc[:start])  # every variant shares the warm-up
