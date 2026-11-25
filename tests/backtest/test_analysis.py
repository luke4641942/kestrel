from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.analysis import analyse, concentration, start_dates
from kestrel.backtest.engine import run_backtest
from kestrel.backtest.metrics import compute_metrics, format_report
from kestrel.data.schema import Interval
from kestrel.strategy import BuyAndHold
from tests.backtest.helpers import FixedBrackets, FixedWeights, bars, config

DAY = timedelta(days=1)
D1 = Interval.D1


def daily(closes: list[float], start: datetime = datetime(2023, 1, 1, tzinfo=UTC)) -> pl.DataFrame:
    """Daily bars whose open equals the previous close, so returns are easy to state."""
    opens = [closes[0], *closes[:-1]]
    return bars(opens, closes=closes, step=DAY, start=start)


def run(strategy: object, frame: pl.DataFrame, costs: bool = False):  # type: ignore[no-untyped-def]
    return run_backtest(strategy, frame, D1, config(costs=costs, band=0), Decimal(1000))  # type: ignore[arg-type]


def rising(n: int = 120) -> pl.DataFrame:
    """A steady climb with mild noise: +0.4% on two days in three, -0.2% on the third."""
    closes, price = [], 100.0
    for i in range(n):
        price *= 0.998 if i % 3 == 0 else 1.004
        closes.append(price)
    return daily(closes)


def test_results_are_reproducible() -> None:
    frame = rising()
    a = analyse(run(BuyAndHold(), frame), run(BuyAndHold(), frame))
    b = analyse(run(BuyAndHold(), frame), run(BuyAndHold(), frame))
    assert a == b


def test_buy_and_hold_against_itself() -> None:
    frame = rising()
    r = run(BuyAndHold(), frame)
    a = analyse(r, r)
    assert a.beta == pytest.approx(1)
    assert a.correlation == pytest.approx(1)
    total = float(r.final_equity) / 1000 - 1
    lo, hi = a.return_ci
    assert lo < total < hi
    assert a.p_profit > 0.95  # a steady climb is profitable in almost every resample
    assert a.min_track_record_days is not None
    assert all(y.strategy == pytest.approx(y.btc) for y in a.years)


def test_half_exposure_is_compared_with_half_of_btc() -> None:
    frame = rising()
    half = run(FixedWeights([0.5] * frame.height), frame)
    a = analyse(half, run(BuyAndHold(), frame))
    assert a.beta == pytest.approx(0.5, abs=0.02)
    # BTC at the same average exposure is close to the strategy itself (no costs here).
    assert a.matched_btc_return == pytest.approx(float(half.final_equity) / 1000 - 1, rel=0.05)


def test_a_losing_history_is_never_credible() -> None:
    frame = daily([100 * 0.99**i for i in range(60)])
    r = run(BuyAndHold(), frame)
    a = analyse(r, r)
    assert a.min_track_record_days is None
    assert a.p_profit < 0.05
    assert a.return_ci[1] < 0


def test_break_even_cost() -> None:
    # Buy at 100 (bar 1 open), sell at 110 (bar 4 open). Zero costs: 1,000 -> 1,100.
    frame = daily([100.0, 100.0, 104.0, 110.0, 110.0, 110.0])
    r = run(FixedWeights([1, 1, 1, 0, 0, 0]), frame)
    a = analyse(r, run(BuyAndHold(), frame))
    assert a.gross_profit == pytest.approx(100)
    assert a.cost_per_round_trip_bps == pytest.approx(0)
    # Traded 1,000 in and 1,100 out: 2,100 of notional. Net is zero when cost per side
    # equals 100 / 2,100, so a round trip of 2 x 100 / 2,100 = 952 bps.
    assert a.break_even_cost_bps == pytest.approx(2 * 100 / 2100 * 1e4)


def test_break_even_is_zero_without_an_edge() -> None:
    frame = daily([100.0, 100.0, 95.0, 90.0, 90.0])
    r = run(FixedWeights([1, 1, 0, 0, 0]), frame, costs=True)
    assert analyse(r, run(BuyAndHold(), frame)).break_even_cost_bps == 0


def test_tails_and_calendar_years() -> None:
    closes = [100.0, 100, 110, 99, 105, 120, 118]
    frame = daily(closes, start=datetime(2023, 12, 28, tzinfo=UTC))
    r = run(BuyAndHold(), frame)
    a = analyse(r, r)
    assert a.worst_day == pytest.approx(99 / 110 - 1)
    assert a.expected_shortfall == pytest.approx(99 / 110 - 1)  # 5% of 7 days rounds to 1
    assert [y.year for y in a.years] == [2023, 2024]
    compounded = math.prod(1 + y.strategy for y in a.years) - 1
    assert compounded == pytest.approx(float(r.final_equity) / 1000 - 1)


def test_holding_periods_match_fifo() -> None:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    n = 420
    weights = [1.0] * 400 + [0.0] * 20  # bought on day 1, sold on day 401
    frame = daily([100 + i * 0.1 for i in range(n)], start=start)
    r = run(FixedWeights(weights), frame)
    a = analyse(r, run(BuyAndHold(), frame))
    assert a.holding_buckets == {"under 1 month": 0, "1 to 12 months": 0, "over 12 months": 1}
    assert a.long_term_gain_share == 1.0
    assert not a.open_at_end


def test_short_holds_and_open_positions() -> None:
    weights = [1.0] * 5 + [0.0] * 5 + [1.0] * 10
    frame = daily([100 + i for i in range(20)])
    a = analyse(run(FixedWeights(weights), frame), run(BuyAndHold(), frame))
    assert a.holding_buckets["under 1 month"] == 1
    assert a.long_term_gain_share == 0.0
    assert a.open_at_end  # the second position is never sold


def test_bracket_runs_get_trade_statistics() -> None:
    # Seven trades: two +3R wins, five -1R losses.
    highs, lows = [100.0] * 30, [100.0] * 30
    entries = {}
    for k, i in enumerate(range(0, 28, 4)):
        entries[i] = (95.0, 115.0)
        if k < 2:
            highs[i + 1] = 116.0
        else:
            lows[i + 1] = 94.0
    frame = bars([100.0] * 30, highs=highs, lows=lows, step=DAY)
    r = run_backtest(FixedBrackets(entries), frame, D1, config(costs=False), Decimal(1000))
    assert [round(float(t.r_multiple)) for t in r.trades] == [3, 3, -1, -1, -1, -1, -1]
    a = analyse(r, run_backtest(BuyAndHold(), frame, D1, config(costs=False), Decimal(1000)))
    assert a.payoff_ratio == pytest.approx(3)
    assert a.expectancy_ci is not None
    lo, hi = a.expectancy_ci
    assert lo < 1 / 7 < hi  # (6 - 5) / 7 R
    assert a.p_positive_expectancy is not None
    # Without the five best trades (both wins and the three smallest losses) only the two
    # biggest losses remain. Each risks 1% of the cash at the time, so they compound.
    pnls = sorted((float(t.pnl) for t in r.trades), reverse=True)
    assert a.profit_without_top5 == pytest.approx(pnls[5] + pnls[6])
    assert a.profit_without_top5 < 0


def test_weight_runs_have_no_trade_statistics() -> None:
    frame = rising()
    a = analyse(run(BuyAndHold(), frame), run(BuyAndHold(), frame))
    assert a.expectancy_ci is None
    assert a.payoff_ratio is None
    assert a.profit_without_top5 is None


def test_too_little_history_for_beta_and_no_trades_for_break_even() -> None:
    from kestrel.backtest.metrics import compute_metrics, format_report

    frame = daily([100.0, 101.0])
    r = run(FixedWeights([0.0, 0.0]), frame)  # never trades
    a = analyse(r, run(BuyAndHold(), frame))
    assert a.beta is None
    assert a.break_even_cost_bps is None
    report = format_report(r, compute_metrics(r), a)
    assert "Break-even" not in report
    assert "beta" not in report
    assert "never credible" in report


def test_report_shows_expectancy_for_bracket_runs() -> None:
    from kestrel.backtest.metrics import compute_metrics, format_report

    frame = bars([100.0] * 12, highs=[100.0, 116.0] + [100.0] * 10, step=DAY)
    r = run_backtest(
        FixedBrackets({0: (95.0, 115.0), 4: (95.0, 115.0)}),
        frame,
        D1,
        config(costs=False),
        Decimal(1000),
    )
    a = analyse(r, run_backtest(BuyAndHold(), frame, D1, config(costs=False), Decimal(1000)))
    assert "Expectancy CI" in format_report(r, compute_metrics(r), a)


def test_break_even_trades_do_not_break_the_payoff_ratio() -> None:
    # Regression: one win and one trade closing flat at exactly 0R divided by zero.
    frame = bars([100.0] * 12, highs=[100.0, 116.0] + [100.0] * 10, step=DAY)
    r = run_backtest(
        FixedBrackets({0: (95.0, 115.0), 4: (95.0, 115.0)}),
        frame,
        D1,
        config(costs=False),
        Decimal(1000),
    )
    assert sorted(float(t.r_multiple) for t in r.trades) == [0.0, 3.0]
    a = analyse(r, run_backtest(BuyAndHold(), frame, D1, config(costs=False), Decimal(1000)))
    assert a.payoff_ratio is None


def test_an_eth_run_is_compared_with_holding_eth() -> None:
    # Regression: the report said "vs BTC" for every market.
    from kestrel.backtest.metrics import compute_metrics, format_report
    from kestrel.config import parse_config

    doc = config(costs=False, band=0).document
    cfg = parse_config({**doc, "instruments": {"ETHUSDT": doc["instruments"]["BTCUSDT"]}})
    frame = rising()
    r = run_backtest(FixedWeights([0.5] * frame.height, "ETHUSDT"), frame, D1, cfg, Decimal(1000))
    b = run_backtest(BuyAndHold("ETHUSDT"), frame, D1, cfg, Decimal(1000))
    assert r.asset == "ETH"
    report = format_report(r, compute_metrics(r), analyse(r, b))
    assert "vs ETH" in report
    assert "BTC" not in report


# ---------- against holding, from every start date ----------


def equity_frame(values: list[float], weights: list[float] | None = None) -> pl.DataFrame:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    return pl.DataFrame(
        {
            "close_time": [start + i * DAY for i in range(len(values))],
            "equity": values,
            "weight": weights or [1.0] * len(values),
        }
    )


def wavy(n: int, crash_at: int | None = None) -> list[float]:
    """A noisy climb (+1%, -0.5%), with a 60% crash on one day if asked."""
    out, v = [], 100.0
    for i in range(n):
        v *= 1.01 if i % 2 else 0.995
        if i == crash_at:
            v *= 0.4
        out.append(v)
    return out


def test_holding_against_itself_never_wins() -> None:
    hold = equity_frame(wavy(800))
    rows = start_dates(hold, hold)
    assert [(r.window_days, r.windows) for r in rows] == [(365, 15), (730, 3)]
    assert all((r.sharpe, r.drawdown, r.total_return) == (0, 0, 0) for r in rows)


def test_a_strategy_that_sidesteps_a_crash_wins_the_windows_containing_it() -> None:
    hold = equity_frame(wavy(800, crash_at=400))
    sidestep = [v * (2.5 if i >= 400 else 1) for i, v in enumerate(wavy(800, crash_at=400))]
    row = start_dates(equity_frame(sidestep), hold)[0]
    # Of 15 one-year starts (days 0, 30 ... 420), those from day 60 to 390 contain the crash.
    assert row.drawdown == pytest.approx(12 / 15)
    assert row.total_return == pytest.approx(12 / 15)


def test_windows_start_once_the_strategy_is_invested() -> None:
    values = wavy(800)
    weights = [0.0] * 200 + [1.0] * 600
    row = start_dates(equity_frame(values, weights), equity_frame(values))[0]
    assert row.windows == len(range(199, 800 - 365, 30))
    never = start_dates(equity_frame(values, [0.0] * 800), equity_frame(values))
    assert [r.windows for r in never] == [0, 0]
    assert never[0].sharpe is None


def test_the_report_shows_start_dates_when_the_run_is_long_enough() -> None:
    long_run = run(BuyAndHold("BTCUSDT"), rising(800))
    a = analyse(long_run, long_run, resamples=50)
    assert a.start_dates[0].windows > 0
    report = format_report(long_run, compute_metrics(long_run), a)
    assert "1-year starts    beats holding from 0% on Sharpe" in report
    short = run(BuyAndHold("BTCUSDT"), rising(120))
    assert "starts" not in format_report(short, compute_metrics(short), analyse(short, short))


def test_drawdown_paths_bracket_the_backtests_drawdown() -> None:
    from kestrel.backtest.analysis import drawdown_paths

    assert drawdown_paths([0.01], 1, 100, 1) is None
    assert drawdown_paths([0.01] * 50, 3, 200, 1) == (0.0, 0.0)  # never falls on any path
    # Ten -10% days in one block, then calm: reshuffled, the fall is split or joined.
    logs = [math.log(0.9)] * 10 + [0.001] * 90
    mid, bad = drawdown_paths(logs, 5, 500, 1)  # type: ignore[misc]
    assert bad <= mid < 0
    # Drawn with replacement, the falling block can come up twice: the 1-in-20 path can be
    # worse than the actual 65% fall.
    assert bad < 0.9**10 - 1
    frame = rising()
    r = run(BuyAndHold(), frame)
    a = analyse(r, r)
    assert a.drawdown_paths is not None
    assert "Drawdown, paths" in format_report(r, compute_metrics(r), a)


def test_concentration_takes_out_the_best_days_and_the_best_year() -> None:
    # Daily marks from 2020-12-30. A mark belongs to the year of its close_time (as in the
    # years table): 2020 is +10% (the 12-31 mark), 2021 is 132 / 110, +20%.
    values = [100.0, 110, 110, 99, 132, 132, 132]
    start = datetime(2020, 12, 30, tzinfo=UTC)
    frame = equity_frame(values).with_columns(
        close_time=pl.Series([start + i * DAY for i in range(len(values))])
    )
    c = concentration(frame, top_days=1)
    assert c is not None
    assert c.total_return == pytest.approx(0.32)
    assert c.without_top_days == pytest.approx(1.32 / (132 / 99) - 1)  # the +33% day removed
    assert (c.best_year, c.best_year_return) == (2021, pytest.approx(0.2))
    assert c.without_best_year == pytest.approx(0.1)  # 2020's +10% remains
    assert concentration(frame, top_days=4).without_top_days is None  # too short to say
    assert concentration(frame.head(2)) is None


def test_every_run_gets_its_concentration_beside_holdings() -> None:
    r = run(BuyAndHold(), rising(400))
    a = analyse(r, r)
    assert a.concentration == a.hold_concentration
    assert a.concentration is not None
    assert a.concentration.without_top_days is not None
    assert a.concentration.without_top_days < a.concentration.total_return
    assert "best days" in "\n".join(format_report(r, compute_metrics(r), analysis=a).splitlines())
