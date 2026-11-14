"""Large-coin baskets: the control, trend across coins, nearness to the 52-week high, and
BTC/ETH dual momentum, on hand-built daily panels (2024-01-01 is a Monday, so every seventh
day from 2024-01-07 is a Sunday decision). Synthetic bars only: research strategies must
not be run on market data until their research notes are frozen."""

from __future__ import annotations

import itertools
import math
import random
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.portfolio import run_portfolio, validated_decisions
from kestrel.data.schema import Interval
from kestrel.strategies.anchor import HighAnchor
from kestrel.strategies.basket import LargeCoinBasket
from kestrel.strategies.monthly_rotation import MonthlyRotation
from kestrel.strategies.rotation import DualMomentum
from kestrel.strategies.trend_basket import TrendBasket
from kestrel.strategy import STRATEGIES, make_portfolio_strategy
from tests.backtest.helpers import config
from tests.backtest.test_portfolio import T0, panel


def steady(daily: float, n: int) -> list[float | None]:
    return [100 * (1 + daily) ** i for i in range(n)]


def coins(spec: dict[str, tuple[list[float | None], float]]) -> pl.DataFrame:
    """Coins with their own daily volumes: {symbol: (prices, volume)}."""
    return pl.concat([panel({s: p}, volume=v) for s, (p, v) in spec.items()]).sort(
        "symbol", "open_time"
    )


def last(strategy: object, p: pl.DataFrame) -> pl.DataFrame:
    d = validated_decisions(strategy, p)  # type: ignore[arg-type]
    return d.filter(pl.col("open_time") == d["open_time"].max())


def random_coins(n_coins: int, days: int, seed: int) -> pl.DataFrame:
    rnd = random.Random(seed)
    out: dict[str, list[float | None]] = {}
    for k in range(n_coins):
        price, path = 100.0, []
        for _ in range(days):
            price *= math.exp(rnd.gauss(0.001, 0.04))
            path.append(price)
        out[f"C{k}"] = path
    return panel(out)


# ---------- the plain basket (control) ----------


def test_the_basket_is_the_most_traded_coins_held_equally() -> None:
    p = coins(
        {
            "BIG": (steady(0.0, 60), 9e9),
            "MID": (steady(0.0, 60), 5e9),
            "SMALL": (steady(0.0, 60), 1e9),
            "THIN": (steady(0.0, 60), 1e6),  # under the 20M floor
        }
    )
    d = last(LargeCoinBasket(top_n=2, min_history_days=30), p)
    assert d["symbol"].to_list() == ["BIG", "MID"]
    assert d["weight"].to_list() == [0.5, 0.5]
    # Too few eligible coins: the empty slots stay in cash.
    d = last(LargeCoinBasket(top_n=5, min_history_days=30), p)
    assert d["weight"].sum() == pytest.approx(3 / 5)


def test_a_coin_needs_its_history_before_joining() -> None:
    young = [None] * 40 + steady(0.0, 20)
    p = coins({"OLD": (steady(0.0, 60), 1e9), "YOUNG": (young, 9e9)})
    d = last(LargeCoinBasket(top_n=2, min_history_days=30), p)
    assert d["symbol"].to_list() == ["OLD"]


def test_every_sunday_is_a_decision_even_with_nothing_eligible() -> None:
    d = validated_decisions(LargeCoinBasket(min_history_days=400), panel({"A": steady(0, 30)}))
    assert d["symbol"].to_list() == [None] * 4
    assert all(t.weekday() == 6 for t in d["open_time"].to_list())


@pytest.mark.parametrize(
    "cls", [LargeCoinBasket, TrendBasket, HighAnchor, DualMomentum, MonthlyRotation]
)
def test_empty_and_intraday_panels(cls: type) -> None:
    p = panel({"BTCUSDT": steady(0.01, 30)})
    assert cls().target_weights(p.clear()).is_empty()
    hourly = p.with_columns(open_time=T0 + pl.duration(hours=pl.int_range(pl.len())))
    with pytest.raises(ValueError, match="daily bars"):
        cls().target_weights(hourly)


# ---------- trend across coins ----------


def trend(**params: int) -> TrendBasket:
    return TrendBasket(**{"min_history_days": 30, "top_n": 2, **params})


def test_each_coin_fills_its_slot_by_its_own_trend() -> None:
    # Smooth paths have zero volatility, so sizing is capped at the full slot.
    p = coins({"UP": (steady(0.01, 200), 9e9), "DOWN": (steady(-0.01, 200), 5e9)})
    d = last(trend(), p)
    assert d["symbol"].to_list() == ["UP"]
    assert d["weight"].to_list() == [0.5]  # DOWN's slot stays in cash


def test_partial_trend_and_high_volatility_fill_less() -> None:
    # 150 days up, then 30 days gently down: only the 20-day vote is down.
    path = steady(0.01, 150) + [None] * 30
    for i in range(150, 180):
        path[i] = path[i - 1] * 0.998  # type: ignore[operator]
    wild = [100 * math.exp(0.002 * i + (0.05 if i % 2 else -0.05)) for i in range(180)]
    p = coins({"MIXED": (path, 9e9), "WILD": (wild, 5e9)})  # type: ignore[dict-item]
    d = last(trend(), p).sort("symbol")
    weights = dict(zip(d["symbol"], d["weight"], strict=True))
    assert weights["MIXED"] == pytest.approx(0.5 * 2 / 3)
    assert 0 < weights["WILD"] < 0.5  # all votes up, but far wilder than the 50% target


def test_cash_until_the_longest_trend_has_filled() -> None:
    p = coins({"UP": (steady(0.01, 130), 9e9)})
    d = validated_decisions(trend(), p)
    held = d.filter(pl.col("symbol").is_not_null())["open_time"].to_list()
    assert held  # the last Sunday, day 125, has a close 120 days earlier
    assert all((t - T0).days >= 120 for t in held)


# ---------- nearness to the 52-week high ----------


def test_the_coins_nearest_their_high_are_held() -> None:
    n = 400
    at_high = steady(0.001, n)
    off_high = steady(0.001, n - 30) + [None] * 30
    for i in range(n - 30, n):
        off_high[i] = off_high[i - 1] * 0.99  # type: ignore[operator]
    far = steady(0.001, n - 30) + [None] * 30
    for i in range(n - 30, n):
        far[i] = far[i - 1] * 0.97  # type: ignore[operator]
    p = coins({"AT": (at_high, 1e9), "OFF": (off_high, 2e9), "FAR": (far, 3e9)})
    d = last(HighAnchor(hold_n=2, basket_n=3), p)
    assert d["symbol"].to_list() == ["AT", "OFF"]
    assert d["weight"].to_list() == [0.5, 0.5]


def test_the_high_uses_bar_highs_and_a_full_year_of_history() -> None:
    # SPIKED's high once reached 200 though no close did, so it is far from its high;
    # FLAT sits at its high.
    p = coins({"SPIKED": (steady(0.0, 380), 9e9), "FLAT": (steady(0.0, 380), 1e9)})
    spike = (pl.col("symbol") == "SPIKED") & (
        pl.col("open_time") == p["open_time"].min() + (p["open_time"][20] - p["open_time"][0])
    )
    p = p.with_columns(high=pl.when(spike).then(200.0).otherwise(pl.col("high")))
    d = validated_decisions(HighAnchor(hold_n=1, basket_n=2), p)
    held = d.filter(pl.col("symbol").is_not_null())
    assert held["symbol"].to_list() == ["FLAT", "FLAT"]  # days 370 and 377: a year of history
    assert all((t - T0).days >= 364 for t in held["open_time"])
    with pytest.raises(ValueError, match="at most basket_n"):
        HighAnchor(hold_n=5, basket_n=3)


# ---------- BTC/ETH dual momentum ----------


def test_holds_the_stronger_of_btc_and_eth_or_cash() -> None:
    p = panel(
        {
            "BTCUSDT": steady(0.002, 100),
            "ETHUSDT": steady(0.004, 100),
            "XYZUSDT": steady(0.05, 100),  # never considered
        }
    )
    d = last(DualMomentum(), p)
    assert d["symbol"].to_list() == ["ETHUSDT"]
    assert d["weight"].to_list() == [1.0]
    falling = panel({"BTCUSDT": steady(-0.001, 100), "ETHUSDT": steady(-0.002, 100)})
    assert last(DualMomentum(), falling)["symbol"].to_list() == [None]


def test_rotation_waits_for_both_histories() -> None:
    p = panel({"BTCUSDT": steady(0.01, 120), "ETHUSDT": [None] * 40 + steady(0.01, 80)})
    d = validated_decisions(DualMomentum(), p)
    held = d.filter(pl.col("symbol").is_not_null())
    assert held.is_empty()  # ETH has only 80 days, under the 90-day lookback
    assert DualMomentum().target_weights(panel({"XYZUSDT": steady(0.0, 10)})).is_empty()


# ---------- all of them ----------


@pytest.mark.parametrize(
    ("key", "params"),
    [
        ("large-basket", {"min_history_days": 30, "top_n": 4}),
        ("trend-basket", {"min_history_days": 120, "top_n": 4}),
        ("high-anchor", {"basket_n": 6}),
    ],
)
def test_no_lookahead_on_random_coins_and_it_trades(key: str, params: dict[str, int]) -> None:
    p = random_coins(8, 420, seed=7)
    result = run_portfolio(
        make_portfolio_strategy(key, params), p, Interval.D1, config(), Decimal(10_000)
    )
    assert result.fills
    assert result.equity["weight"].max() <= 1 + 1e-9


def test_rotation_has_no_lookahead_and_trades() -> None:
    p = random_coins(2, 400, seed=3).with_columns(
        symbol=pl.col("symbol").replace({"C0": "BTCUSDT", "C1": "ETHUSDT"})
    )
    result = run_portfolio(
        make_portfolio_strategy("btc-eth-rotation"), p, Interval.D1, config(), Decimal(10_000)
    )
    assert result.fills


def test_registered() -> None:
    kinds = {k: (STRATEGIES[k].kind, STRATEGIES[k].universe) for k in STRATEGIES}
    assert kinds["large-basket"] == ("control", True)
    for key in ("trend-basket", "high-anchor", "btc-eth-rotation"):
        assert kinds[key] == ("research", True)


def test_sundays_are_seven_days_apart() -> None:
    d = validated_decisions(LargeCoinBasket(min_history_days=1), panel({"A": steady(0, 60)}))
    days = sorted(set(d["open_time"].to_list()))
    assert all(b - a == days[1] - days[0] for a, b in itertools.pairwise(days))


# ---------- BTC/ETH/SOL monthly rotation ----------


def rotation(**params: int) -> MonthlyRotation:
    return MonthlyRotation(**{"lookback_days": 20, "trend_days": 10, **params})


def test_rotation_holds_the_top_two_in_uptrends_at_month_ends() -> None:
    # 2024-01-01 + 60 days: month ends on Jan 31 (day 30) and Feb 29 (day 59).
    p = coins({
        "BTCUSDT": (steady(0.01, 61), 9e9),
        "ETHUSDT": (steady(0.02, 61), 9e9),
        "SOLUSDT": (steady(0.005, 61), 9e9),
        "DOGEUSDT": (steady(0.05, 61), 9e9),  # not one of the three: never held
    })  # fmt: skip
    d = validated_decisions(rotation(), p)
    assert sorted({t.day for t in d["open_time"].to_list()}) == [29, 31]
    feb = d.filter(pl.col("open_time").dt.month() == 2)
    assert feb["symbol"].to_list() == ["BTCUSDT", "ETHUSDT"]  # SOL rose least; DOGE isn't one
    assert feb["weight"].to_list() == [0.5, 0.5]


def test_rotation_leaves_a_falling_coins_slot_in_cash() -> None:
    p = coins({
        "BTCUSDT": (steady(0.01, 61), 9e9),
        "ETHUSDT": (steady(-0.01, 61), 9e9),  # below its average: not eligible
    })  # fmt: skip
    feb = last(rotation(), p)
    assert feb["symbol"].to_list() == ["BTCUSDT"]
    assert feb["weight"].to_list() == [0.5]
    down = coins({"BTCUSDT": (steady(-0.01, 61), 9e9)})
    nothing = last(rotation(), down)
    assert nothing["symbol"].to_list() == [None]  # a month end holding nothing


def test_rotation_is_registered() -> None:
    spec = STRATEGIES["monthly-rotation"]
    assert (spec.kind, spec.universe) == ("research", True)
    assert make_portfolio_strategy("monthly-rotation", {"lookback_days": 180}).name == (
        "BTC/ETH/SOL monthly rotation, top 2 by 180d"
    )
