"""The two engines must agree.

The event-driven engine (bar by bar, exact decimals, exchange rules) and the vectorised
engine (whole columns at once, floats) are independent implementations of one model.
Where the event engine's extra realism can't bite (an account big enough that quantity
rounding and minimums don't matter, small enough to stay under the 1% participation cap,
and no no-trade band) they must produce the same backtest.

Tolerances come from measuring both engines on real BTCUSDT 4h data, 2022-01..2025-10,
at 50,000 USDT: daily returns agreed within 2.4e-5, Sharpe within 0.001, costs within
0.24%, final equity within 0.13% (the busiest strategy, after four years of
compounding). The bounds below leave a little headroom over those.
"""

from __future__ import annotations

import math
import random
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.engine import run_backtest
from kestrel.backtest.metrics import compute_metrics, daily_equity
from kestrel.backtest.vectorised import run_vectorised
from kestrel.data.schema import Interval
from kestrel.strategy import BuyAndHold, ChannelBreakout, MovingAverageTrend
from tests.backtest.helpers import FixedWeights, bars, config

H4 = Interval.H4
CASH = Decimal(50_000)


def random_walk(n: int = 3000, seed: int = 7) -> pl.DataFrame:
    rnd = random.Random(seed)
    opens, closes, highs, lows = [], [], [], []
    price = 30_000.0
    for _ in range(n):
        o = price * math.exp(rnd.gauss(0, 0.001))  # a small gap from the previous close
        c = o * math.exp(rnd.gauss(0.0001, 0.012))
        opens.append(o)
        closes.append(c)
        highs.append(max(o, c) * (1 + abs(rnd.gauss(0, 0.004))))
        lows.append(min(o, c) * (1 - abs(rnd.gauss(0, 0.004))))
        price = c
    return bars(opens, closes=closes, highs=highs, lows=lows, quote_volume=200e6)


STRATEGIES = {
    "buy-and-hold": BuyAndHold(),
    "ma 20/100": MovingAverageTrend(fast=20, slow=100),
    "ma 5/30 (high turnover)": MovingAverageTrend(fast=5, slow=30),
    "fractional weights": FixedWeights([0.3 if (i // 50) % 2 else 0.8 for i in range(3000)]),
}


@pytest.fixture(scope="module")
def frame() -> pl.DataFrame:
    return random_walk()


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_engines_agree(frame: pl.DataFrame, name: str) -> None:
    strategy = STRATEGIES[name]
    cfg = config(band=0)
    event = run_backtest(strategy, frame, H4, cfg, CASH)
    fast = run_vectorised(strategy, frame, H4, cfg, CASH)
    assert not any("volume" in r.rejection.reason for r in event.rejections)  # under the cap

    e, v = compute_metrics(event), compute_metrics(fast)
    daily_e = daily_equity(event)["equity"].pct_change().drop_nulls()
    daily_v = daily_equity(fast)["equity"].pct_change().drop_nulls()

    assert (daily_e - daily_v).abs().max() < 1e-4
    assert abs(e.sharpe - v.sharpe) < 0.005
    assert abs(float(event.final_equity) / float(fast.final_equity) - 1) < 2e-3
    assert abs(e.total_costs / v.total_costs - 1) < 0.01
    assert e.exposure == pytest.approx(v.exposure, abs=1e-3)


def test_with_zero_costs_buy_and_hold_is_exact_in_both() -> None:
    frame = bars([100.0, 100.0, 110.0, 120.0], closes=[100.0, 105.0, 115.0, 132.0])
    cfg = config(costs=False, band=0)
    event = run_backtest(BuyAndHold(), frame, H4, cfg, Decimal(1000))
    fast = run_vectorised(BuyAndHold(), frame, H4, cfg, Decimal(1000))
    # 10 BTC bought at bar 1's open (100); worth 1,320 at the last close.
    assert float(event.final_equity) == 1320
    assert fast.equity["equity"][-1] == pytest.approx(1320)
    assert fast.equity["equity"].to_list() == pytest.approx(event.equity["equity"].to_list())


def test_vectorised_reports_costs_and_rebalances(frame: pl.DataFrame) -> None:
    fast = run_vectorised(MovingAverageTrend(fast=5, slow=30), frame, H4, config(band=0), CASH)
    m = compute_metrics(fast)
    assert fast.engine == "vectorised"
    assert fast.summary is not None
    assert m.trades == fast.summary.rebalances > 0
    assert m.total_costs == pytest.approx(
        fast.summary.fees + fast.summary.spread_costs + fast.summary.impact_costs
    )
    assert min(m.fees, m.spread_costs, m.impact_costs) > 0


def test_vectorised_refuses_what_it_cannot_model() -> None:
    frame = random_walk(200)
    with pytest.raises(ValueError, match="weight strategies only"):
        run_vectorised(ChannelBreakout(), frame, H4, config(), CASH)
    dead = bars([100.0] * 5, quote_volume=0)
    with pytest.raises(ValueError, match="no volume"):
        run_vectorised(BuyAndHold(), dead, H4, config(), CASH)


def test_vectorised_applies_the_same_input_checks() -> None:
    from datetime import UTC, datetime

    from kestrel.backtest.engine import HoldoutError

    late = bars([100.0] * 5, start=datetime(2025, 10, 31, 16, tzinfo=UTC))
    with pytest.raises(HoldoutError):
        run_vectorised(BuyAndHold(), late, H4, config(), CASH)
    assert run_vectorised(BuyAndHold(), late, H4, config(), CASH, allow_holdout=True).used_holdout
    with pytest.raises(ValueError, match="sorted"):
        run_vectorised(BuyAndHold(), random_walk(50).reverse(), H4, config(), CASH)
