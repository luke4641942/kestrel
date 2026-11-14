"""Squeeze breakout, on synthetic daily closes only: the strategy must not be run on market
data until its research note is frozen. Short windows keep the cases small: squeeze_bars
40, trend_days 30."""

from __future__ import annotations

from datetime import timedelta

import pytest

from kestrel.strategies.squeeze import SqueezeBreakout
from kestrel.strategy import STRATEGIES, validated_weights
from tests.backtest.helpers import bars

DAY = timedelta(days=1)


def closes(after: list[float], level: float = 100.0) -> list[float]:
    """60 wide days (+-3), 25 quiet days (+-0.1), then ``after``."""
    wide = [level + (3 if i % 2 else -3) for i in range(60)]
    quiet = [level + (0.1 if i % 2 else -0.1) for i in range(25)]
    return wide + quiet + after


def weights(c: list[float], **params: int) -> list[float]:
    s = SqueezeBreakout(**{"squeeze_bars": 40, "trend_days": 30, **params})
    return validated_weights(s, bars(c, step=DAY)).to_list()


def test_a_break_above_the_band_after_a_squeeze_buys_and_the_middle_band_sells() -> None:
    w = weights(closes([103.0, 106.0, 108.0, 99.0, 99.0]))
    assert w[:85] == [0.0] * 85
    assert w[85:88] == [1.0, 1.0, 1.0]  # above the upper band, then held
    assert w[88] == 0.0  # closed below the 20-day average


def test_no_break_without_a_recent_squeeze() -> None:
    # The bands widen again (+-1.5) for 30 days before the break.
    late = closes([101.5, 98.5] * 15 + [107.0])
    assert weights(late, recent_bars=5)[-1] == 0.0
    assert weights(late, recent_bars=40)[-1] == 1.0


def test_no_break_below_the_trend() -> None:
    falling = [130.0 - i * 0.5 for i in range(60)]
    quiet = [100 + (0.1 if i % 2 else -0.1) for i in range(25)]
    # Above the band, but under the 80-day average, which still holds the fall.
    assert weights(falling + quiet + [101.0], trend_days=80)[-1] == 0.0
    assert weights(falling + quiet + [101.0], trend_days=20)[-1] == 1.0


def test_daily_bars_only_and_registered() -> None:
    with pytest.raises(ValueError, match="daily bars"):
        SqueezeBreakout().target_weights(bars([1.0] * 5, step=timedelta(hours=4)))
    spec = STRATEGIES["squeeze-breakout"]
    assert spec.kind == "research"
    assert [p.name for p in spec.params] == ["squeeze_bars", "recent_bars", "trend_days"]
