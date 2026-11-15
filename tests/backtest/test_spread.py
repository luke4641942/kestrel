"""Spreads banded by each coin's trailing median volume."""

from __future__ import annotations

import polars as pl
import pytest

from kestrel.backtest.spread import with_half_spread

TIERS = [(500e6, 0.0001), (100e6, 0.0003), (20e6, 0.0008), (0, 0.0015)]


def coin(volumes: list[float], symbol: str = "X") -> pl.DataFrame:
    return pl.DataFrame({"symbol": [symbol] * len(volumes), "quote_volume": volumes})


@pytest.mark.parametrize(
    ("volume", "half"),
    [(2e9, 0.0001), (500e6, 0.0001), (200e6, 0.0003), (50e6, 0.0008), (5e6, 0.0015)],
)
def test_each_band(volume: float, half: float) -> None:
    out = with_half_spread(coin([volume] * 5), window=3, tiers=TIERS, by=["symbol"])
    assert out["half_spread"][-1] == half


def test_too_little_history_gets_the_widest_band() -> None:
    out = with_half_spread(coin([2e9] * 5), window=3, tiers=TIERS, by=["symbol"])
    assert out["half_spread"].to_list()[:2] == [0.0015, 0.0015]
    assert out["half_spread"][2] == 0.0001


def test_the_median_ignores_one_wild_day() -> None:
    out = with_half_spread(coin([50e6, 50e6, 9e9]), window=3, tiers=TIERS, by=["symbol"])
    assert out["half_spread"][-1] == 0.0008  # one huge day doesn't make a coin liquid


def test_coins_and_histories_never_share_a_window() -> None:
    frame = pl.concat([coin([2e9] * 3, "A"), coin([5e6] * 3, "B")])
    out = with_half_spread(frame, window=3, tiers=TIERS, by=["symbol"])
    assert out.filter(pl.col("symbol") == "B")["half_spread"].to_list() == [0.0015] * 3


def test_only_closed_bars_are_used() -> None:
    frame = coin([1e6, 3e8, 3e8, 2e9, 2e9, 2e9])
    full = with_half_spread(frame, 3, TIERS, ["symbol"])["half_spread"][:4]
    early = with_half_spread(frame.head(4), 3, TIERS, ["symbol"])["half_spread"]
    assert full.to_list() == early.to_list()


def test_intraday_bars_pay_the_band_of_their_daily_volume() -> None:
    """5m bars of 0.5M each are 144M a day: the 100M+ band (3 bps), not the widest (15 bps)
    that 30 bars' worth of volume would suggest. A bar pays the band of the 30 complete
    days before its own, so the first 30 days pay the widest."""
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from kestrel.backtest.engine import half_spreads
    from tests.backtest.helpers import bars, config

    n = 288 * 32  # 32 days of 5m bars
    frame = bars(
        [100.0] * n,
        start=datetime(2024, 1, 1, tzinfo=UTC),
        step=timedelta(minutes=5),
        quote_volume=0.5e6,
    )
    paid = half_spreads(frame, config(), "SOLUSDT")
    assert paid[0] == Decimal("0.0015")  # day 1: no complete history yet
    assert paid[288 * 30 - 1] == Decimal("0.0015")  # day 30: only 29 complete days before
    assert paid[288 * 30] == Decimal("0.0003")  # day 31: 30 complete days of 144M
    assert paid[-1] == Decimal("0.0003")
    assert half_spreads(frame, config(), "BTCUSDT") == [None] * n  # its own instrument entry


def test_daily_bars_keep_the_window_of_days_including_their_own() -> None:
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from kestrel.backtest.engine import half_spreads
    from tests.backtest.helpers import bars, config

    frame = bars(
        [100.0] * 31,
        start=datetime(2024, 1, 1, tzinfo=UTC),
        step=timedelta(days=1),
        quote_volume=150e6,
    )
    paid = half_spreads(frame, config(), "SOLUSDT")
    assert paid[28] == Decimal("0.0015")
    assert paid[29] == Decimal("0.0003")  # the 30th day's close completes the window
