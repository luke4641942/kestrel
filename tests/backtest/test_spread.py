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
