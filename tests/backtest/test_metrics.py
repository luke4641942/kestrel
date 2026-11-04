from __future__ import annotations

import itertools
import math
import statistics
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.engine import BacktestResult, run_backtest
from kestrel.backtest.metrics import compute_metrics, daily_equity, format_report
from kestrel.data.schema import Interval
from kestrel.strategy import BuyAndHold
from tests.backtest.helpers import bars, config

DAY = timedelta(days=1)
T0 = datetime(2024, 1, 1, tzinfo=UTC)


def result_from(equity: list[float], step: timedelta = DAY) -> BacktestResult:
    """A result with a given equity path, marked at 00:00 UTC closes."""
    frame = pl.DataFrame(
        {
            "close_time": [T0 + i * step for i in range(len(equity))],
            "equity": equity,
            "cash": [0.0] * len(equity),
            "position": [1.0] * len(equity),
            "weight": [1.0] * len(equity),
        }
    ).with_columns(pl.col("close_time").dt.cast_time_unit("us"))
    return BacktestResult("test", "BTCUSDT", Interval.D1, Decimal(str(equity[0])), frame)


def test_daily_marks_only_at_midnight_for_4h_bars() -> None:
    result = run_backtest(BuyAndHold(), bars([100.0] * 18), Interval.H4, config(), Decimal(1000))
    marks = daily_equity(result)
    assert marks.height == 4  # the starting cash at 00:00, then 18 x 4h bars = 3 day closes
    assert all(t.hour == 0 and t.minute == 0 for t in marks["close_time"])


def test_total_return_counts_from_the_starting_cash() -> None:
    # Regression: the first daily mark used to be the first midnight *after* the buy,
    # silently dropping the first day's move from the return.
    opens = [100.0 + 5 * i for i in range(18)]
    result = run_backtest(
        BuyAndHold(),
        bars(opens, closes=[o + 5 for o in opens]),
        Interval.H4,
        config(costs=False),
        Decimal(1000),
    )
    m = compute_metrics(result)
    assert m.total_return == pytest.approx(float(result.final_equity) / 1000 - 1)


def test_sharpe_matches_an_independent_calculation() -> None:
    equity = [100.0]
    for r in [0.01, -0.005, 0.02, 0.0, -0.01, 0.015, 0.005, -0.002, 0.01, 0.003]:
        equity.append(equity[-1] * (1 + r))
    m = compute_metrics(result_from(equity))
    rets = [b / a - 1 for a, b in itertools.pairwise(equity)]
    expected = statistics.mean(rets) / statistics.stdev(rets) * math.sqrt(365)
    assert m.sharpe == pytest.approx(expected)
    assert m.sharpe_ci[0] < m.sharpe < m.sharpe_ci[1]
    assert 0.5 < m.psr < 1  # positive Sharpe, but far from certain on 10 days


def test_confidence_interval_narrows_with_more_data() -> None:
    def path(days: int) -> list[float]:
        eq = [100.0]
        for i in range(days):
            eq.append(eq[-1] * (1 + (0.012 if i % 2 else -0.008)))
        return eq

    short, long = compute_metrics(result_from(path(30))), compute_metrics(result_from(path(600)))
    width = lambda m: m.sharpe_ci[1] - m.sharpe_ci[0]  # noqa: E731
    assert width(long) < width(short) / 3
    assert long.psr > short.psr


def test_drawdown_depth_and_longest_time_underwater() -> None:
    # Peak 120 on day 1, trough 90 on day 2, still under on day 3, new high on day 4.
    m = compute_metrics(result_from([100, 120, 90, 95, 130]))
    assert m.max_drawdown == pytest.approx(90 / 120 - 1)
    assert m.longest_underwater == 2 * DAY


def test_cagr_of_a_doubling_over_one_year() -> None:
    equity = [100 * 2 ** (i / 365) for i in range(366)]
    m = compute_metrics(result_from(equity))
    assert m.total_return == pytest.approx(1.0)
    assert m.cagr == pytest.approx(1.0)


def test_flat_equity_has_no_sharpe() -> None:
    m = compute_metrics(result_from([100.0] * 5))
    assert math.isnan(m.sharpe)
    assert math.isnan(m.psr)
    assert "n/a" in format_report(result_from([100.0] * 5), m)


def test_needs_enough_days() -> None:
    with pytest.raises(ValueError, match="three daily marks"):
        compute_metrics(result_from([100.0, 101.0]))


def test_report_flags_few_trades_and_shows_costs() -> None:
    result = run_backtest(
        BuyAndHold(),
        bars([100.0 + i for i in range(30)], step=DAY),
        Interval.D1,
        config(),
        Decimal(1000),
    )
    m = compute_metrics(result)
    report = format_report(result, m)
    assert m.trades == 1
    assert "too few to trust" in report
    assert m.total_costs > 0
    assert f"{m.total_costs:,.4f}" in report
    assert "only strategy tried" in report
