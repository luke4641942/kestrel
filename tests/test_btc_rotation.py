"""The BTC-regime rotation graduates: Sunday decisions, the regime with its margin, the
momentum pick, volatility sizing, the history rule, and no lookahead."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import polars as pl

from kestrel.backtest.portfolio import validated_decisions
from kestrel.strategies.btc_rotation import BtcRegimeRotation, BtcRegimeRotation4
from kestrel.strategy import make_portfolio_strategy
from tests.backtest.test_portfolio import panel

MONDAY = datetime(2023, 1, 2, tzinfo=UTC)
DAYS = 460


def walk(drift: float, seed: int, vol: float = 0.02) -> list[float | None]:
    rnd = random.Random(seed)
    price, out = 100.0, []
    for _ in range(DAYS):
        price *= math.exp(drift + rnd.gauss(0, vol))
        out.append(price)
    return out


def coins(btc_drift: float = 0.004) -> pl.DataFrame:
    return panel(
        {
            "BTCUSDT": walk(btc_drift, 1),
            "ETHUSDT": walk(0.006, 2),  # the strongest
            "SOLUSDT": walk(0.001, 3),
            "BNBUSDT": walk(-0.002, 4),  # the weakest
        },
        start=MONDAY,
    )


def test_it_decides_only_on_sundays_and_picks_the_strongest() -> None:
    out = BtcRegimeRotation4(top_n=1).target_weights(coins())
    assert out.height > 0
    assert (out["open_time"].dt.weekday() == 7).all()
    held = out.filter(pl.col("symbol").is_not_null())
    assert held["symbol"].value_counts().sort("count")["symbol"][-1] == "ETHUSDT"
    assert (held["weight"] > 0).all()
    assert (held["weight"] <= 1.0).all()


def test_nothing_is_held_before_a_year_of_history() -> None:
    out = BtcRegimeRotation4(top_n=1).target_weights(coins())
    early = out.filter(pl.col("open_time") < MONDAY + timedelta(days=364))
    assert early["symbol"].is_null().all()


def test_a_falling_btc_holds_cash() -> None:
    out = BtcRegimeRotation4(top_n=1).target_weights(coins(btc_drift=-0.004))
    late = out.filter(pl.col("open_time") > MONDAY + timedelta(days=400))
    assert late["symbol"].is_null().all()


def test_the_margin_and_the_volatility_target_cut_exposure() -> None:
    wide = BtcRegimeRotation4(top_n=1, margin_pct=10).target_weights(coins())
    narrow = BtcRegimeRotation4(top_n=1, margin_pct=0).target_weights(coins())
    held = lambda d: d.filter(pl.col("symbol").is_not_null()).height  # noqa: E731
    assert held(wide) < held(narrow)
    low = BtcRegimeRotation4(top_n=1, target_vol_pct=10).target_weights(coins())
    high = BtcRegimeRotation4(top_n=1, target_vol_pct=100).target_weights(coins())
    assert low["weight"].sum() < high["weight"].sum()


def test_top_n_splits_equity_and_the_eight_coin_set_ignores_other_coins() -> None:
    two = BtcRegimeRotation4(top_n=2).target_weights(coins())
    per_day = two.filter(pl.col("symbol").is_not_null()).group_by("open_time").len()
    assert per_day["len"].max() == 2
    assert (two["weight"] <= 0.5).all()
    g1 = BtcRegimeRotation(top_n=3)
    extra = coins().vstack(panel({"XRPUSDT": walk(0.02, 9)}, start=MONDAY))
    assert "XRPUSDT" not in g1.target_weights(extra)["symbol"].to_list()  # not in its set


def test_no_lookahead_and_the_registry_builds_both() -> None:
    for key, expected in (
        ("btc-rotation-8", BtcRegimeRotation),
        ("btc-rotation-4", BtcRegimeRotation4),
    ):
        strategy = make_portfolio_strategy(key, {})
        assert type(strategy) is expected
        validated_decisions(strategy, coins())  # raises on any lookahead
