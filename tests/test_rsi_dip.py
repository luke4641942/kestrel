"""RSI dip-buying, on synthetic daily bars only: the strategy must not be run on market data
until its research note is frozen. Highs and lows are the open and close (tests.backtest
.helpers.bars), and each open is the previous close."""

from __future__ import annotations

from datetime import timedelta

import pytest

from kestrel.strategies.rsi_dip import RsiDip
from kestrel.strategy import STRATEGIES, validated_weights
from tests.backtest.helpers import bars

DAY = timedelta(days=1)


def frame(closes: list[float]):  # type: ignore[no-untyped-def]
    return bars([closes[0], *closes[:-1]], closes=closes, step=DAY)


def weights(closes: list[float], **params: int) -> list[float]:
    return validated_weights(RsiDip(**params), frame(closes)).to_list()


RISE = [100.0 + i for i in range(10)]  # 100..109


def test_buys_a_sharp_dip_and_sells_the_first_close_above_yesterdays_high() -> None:
    w = weights(RISE + [104.0, 100.0, 101.0, 103.0], trend_days=0)
    assert w[:11] == [0.0] * 11  # one down day: RSI(2) 16.7, not below 10
    assert w[11] == 1.0  # the second: RSI(2) 7.1
    assert w[12] == 1.0  # 101 isn't above yesterday's high (104, the bar's open)
    assert w[13] == 0.0  # 103 is above yesterday's high of 101


def test_the_trend_filter_blocks_dips_below_the_average() -> None:
    dip = RISE + [104.0, 100.0]
    assert weights(dip, trend_days=5)[11] == 0.0  # 100 is under the 5-day average (105.6)
    assert weights(dip, trend_days=0)[11] == 1.0


def test_g2s_rsi_exit_and_time_stop() -> None:
    slide = RISE + [104.0, 100.0] + [99.9 - i * 0.1 for i in range(11)]
    held = weights(slide, trend_days=0, exit_rsi_above=90)
    assert held[11:] == [1.0] * 12  # bought at the second fall; RSI never bounces above 90
    stopped = weights(slide, trend_days=0, exit_rsi_above=90, max_days=10)
    assert stopped[11:21] == [1.0] * 10
    assert stopped[21] == 0.0  # the 10th close since buying


def test_daily_bars_only_and_registered() -> None:
    with pytest.raises(ValueError, match="daily bars"):
        RsiDip().target_weights(bars([1.0] * 5, step=timedelta(hours=4)))
    spec = STRATEGIES["rsi-dip"]
    assert spec.kind == "research"
    assert [p.name for p in spec.params] == [
        "rsi_bars", "rsi_below", "trend_days", "exit_rsi_above", "max_days",
    ]  # fmt: skip
