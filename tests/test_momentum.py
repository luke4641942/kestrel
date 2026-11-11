"""Cross-sectional momentum on hand-built daily panels (2024-01-01 is a Monday, so every
seventh day from 2024-01-07 is a Sunday decision). Synthetic bars only: the strategy must
not be run on market data until its research note is frozen."""

from __future__ import annotations

import itertools
import math
import random
from datetime import timedelta
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.portfolio import run_portfolio, validated_decisions
from kestrel.data.schema import Interval
from kestrel.strategies.momentum import CrossSectionalMomentum
from kestrel.strategy import STRATEGIES, make_portfolio_strategy, make_strategy
from tests.backtest.helpers import config
from tests.backtest.test_portfolio import T0, panel

DAYS = 70


def steady(daily: float, n: int = DAYS) -> list[float | None]:
    return [100 * (1 + daily) ** i for i in range(n)]


def strategy(**params: int) -> CrossSectionalMomentum:
    return CrossSectionalMomentum(**{"min_history_days": 30, "top_n": 2, **params})


def last_decision(p: pl.DataFrame, **params: int) -> pl.DataFrame:
    d = validated_decisions(strategy(**params), p)
    return d.filter(pl.col("open_time") == d["open_time"].max())


def test_the_strongest_coins_are_held_equally() -> None:
    p = panel({"FAST": steady(0.02), "MID": steady(0.01), "SLOW": steady(0.005)})
    d = last_decision(p)
    assert d["symbol"].to_list() == ["FAST", "MID"]
    assert d["weight"].to_list() == [0.5, 0.5]
    assert d["open_time"][0].weekday() == 6  # a Sunday


def test_a_selected_coin_in_its_own_downtrend_leaves_its_slot_in_cash() -> None:
    # Top 2 are UP and DOWN_LESS (falling least); DOWN_LESS's own trend is down, so only
    # UP is held and the other half stays in cash.
    p = panel({"UP": steady(0.01), "DOWN_LESS": steady(-0.001), "DOWN_MORE": steady(-0.02)})
    d = last_decision(p)
    assert d["symbol"].to_list() == ["UP"]
    assert d["weight"].to_list() == [0.5]


def test_a_falling_market_is_all_cash() -> None:
    p = panel({"A": steady(-0.01), "B": steady(-0.02)})
    d = last_decision(p)
    assert d["symbol"].to_list() == [None]
    assert d["weight"].to_list() == [0.0]


def test_thin_coins_are_never_ranked() -> None:
    liquid = panel({"A": steady(0.005)})
    thin = panel({"THIN": steady(0.05)}, volume=1e6)  # 1M a day, under the 20M floor
    d = last_decision(pl.concat([liquid, thin]).sort("symbol", "open_time"))
    assert d["symbol"].to_list() == ["A"]


def test_a_gap_restarts_a_coins_history() -> None:
    # REUSED vanishes for 10 days (a relisting under the same symbol): it needs 30 days of
    # new history before it can be ranked again, and its return never spans the gap.
    prices = steady(0.03)
    prices[30:40] = [None] * 10
    p = panel({"A": steady(0.005), "REUSED": prices})
    decisions = validated_decisions(strategy(), p)
    held = decisions.filter(pl.col("symbol") == "REUSED")["open_time"].to_list()
    gap_end = T0 + timedelta(days=40)
    assert held, "it is ranked before the gap"
    assert all(t < T0 + timedelta(days=30) or t >= gap_end + timedelta(days=29) for t in held)


def test_every_sunday_is_a_decision() -> None:
    d = validated_decisions(strategy(), panel({"A": steady(0.01)}))
    sundays = sorted(set(d["open_time"].to_list()))
    assert sundays[0] == T0 + timedelta(days=6)
    assert all(b - a == timedelta(days=7) for a, b in itertools.pairwise(sundays))


def test_daily_bars_are_required() -> None:
    hourly = panel({"A": steady(0.01)}).with_columns(
        open_time=T0 + pl.duration(hours=pl.int_range(pl.len()))
    )
    with pytest.raises(ValueError, match="daily bars"):
        strategy().target_weights(hourly)


def test_lookbacks_must_increase() -> None:
    with pytest.raises(ValueError, match="must increase"):
        CrossSectionalMomentum(fast_days=14, mid_days=14)


def test_an_empty_panel_has_no_decisions() -> None:
    empty = panel({"A": steady(0.01)}).clear()
    assert strategy().target_weights(empty).is_empty()


def test_no_lookahead_on_random_coins_and_it_trades() -> None:
    rnd = random.Random(11)
    coins = {}
    for k in range(12):
        price, path = 100.0, []
        for _ in range(240):
            price *= math.exp(rnd.gauss(0.0005, 0.04))
            path.append(price)
        coins[f"C{k}"] = path
    p = panel(coins)
    result = run_portfolio(
        make_portfolio_strategy("xs-momentum", {"min_history_days": 30}),
        p,
        Interval.D1,
        config(),
        Decimal(1000),
    )
    assert result.fills  # it found coins to hold in noise; whether that pays is the note's test
    assert result.equity["weight"].max() <= 1


def test_registered_as_a_universe_research_strategy() -> None:
    spec = STRATEGIES["xs-momentum"]
    assert (spec.kind, spec.universe) == ("research", True)
    with pytest.raises(ValueError, match="whole coin universe"):
        make_strategy("xs-momentum", "BTCUSDT")
    with pytest.raises(ValueError, match="trades one market"):
        make_portfolio_strategy("buy-and-hold")
