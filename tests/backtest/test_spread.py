"""The Abdi-Ranaldo spread estimator, on bars whose spread is known by construction."""

from __future__ import annotations

import math

import polars as pl
import pytest

from kestrel.backtest.spread import with_half_spread


def bouncing(n: int, half: float = 0.005, symbol: str = "X") -> pl.DataFrame:
    """The efficient price stays at 100 (its range is symmetric around it) while each
    close lands on the bid or the ask, alternately: a pure bid-ask bounce."""
    closes = [100 * math.exp(half if i % 2 else -half) for i in range(n)]
    return pl.DataFrame(
        {
            "symbol": [symbol] * n,
            "high": [101.0] * n,
            "low": [100.0**2 / 101] * n,  # log mid-range = log 100
            "close": closes,
        }
    )


def test_a_pure_bounce_recovers_its_half_spread() -> None:
    out = with_half_spread(bouncing(40), window=30, floor=0.0001, by=["symbol"])
    assert out["half_spread"][-1] == pytest.approx(0.005)


def test_floor_until_the_window_has_filled_and_never_below_it() -> None:
    out = with_half_spread(bouncing(40, half=0.00001), window=30, floor=0.0001, by=["symbol"])
    assert out["half_spread"].to_list()[:30] == [0.0001] * 30  # 30 terms need 31 bars
    assert out["half_spread"].min() == 0.0001  # the true 0.1 bp is below the 1 bp floor


def test_each_coin_and_each_unbroken_history_is_estimated_separately() -> None:
    frame = pl.concat([bouncing(40, 0.005, "A"), bouncing(40, 0.002, "B")])
    out = with_half_spread(frame, window=30, floor=0.0001, by=["symbol"])
    last = out.group_by("symbol").agg(pl.col("half_spread").last()).sort("symbol")
    assert last["half_spread"].to_list() == pytest.approx([0.005, 0.002])
    # B's first rows are floored: A's history never leaks into B's window.
    assert out.filter(pl.col("symbol") == "B")["half_spread"][0] == 0.0001


def test_only_closed_bars_are_used() -> None:
    frame = bouncing(40)
    a = with_half_spread(frame, 30, 0.0001, ["symbol"])["half_spread"][:35]
    b = with_half_spread(frame.head(35), 30, 0.0001, ["symbol"])["half_spread"]
    assert a.to_list() == b.to_list()
