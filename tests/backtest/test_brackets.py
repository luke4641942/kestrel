"""Stops, targets and risk-based sizing. Zero costs unless stated, so every number can
be checked by hand: 1,000 USDT, 1% risk = 10 USDT. Entry at 100 with the stop at 95 is
5 of risk per unit, so 2 units; a 3R target is 115."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.engine import run_backtest
from kestrel.backtest.metrics import compute_metrics, format_report
from kestrel.data.schema import Interval
from kestrel.strategy import ChannelBreakout, LookaheadError, validated_brackets
from tests.backtest.helpers import FixedBrackets, bars, config

D = Decimal
H4 = Interval.H4
DAY = timedelta(days=1)
SIGNAL = {0: (95.0, 115.0)}  # decided at bar 0's close, filled at bar 1's open


def run(frame: pl.DataFrame, entries: dict[int, tuple[float, float]] = SIGNAL, **cfg: object):  # type: ignore[no-untyped-def]
    return run_backtest(FixedBrackets(entries), frame, H4, config(costs=False, **cfg), D(1000))  # type: ignore[arg-type]


def test_target_is_plus_three_r() -> None:
    frame = bars(
        [100, 100, 103, 110],
        closes=[100, 103, 110, 112],
        highs=[100, 104, 111, 116],
        lows=[99, 99, 102, 109],
    )
    (t,) = run(frame).trades
    assert t.quantity == D(2)
    assert t.risk == D(10)
    assert t.exit_reason == "target"
    assert t.exit_price == D(115)
    assert t.r_multiple == D(3)


def test_stop_is_minus_one_r() -> None:
    frame = bars([100, 100, 98, 97], highs=[100, 101, 99, 98], lows=[99, 99, 94, 96])
    (t,) = run(frame).trades
    assert t.exit_reason == "stop"
    assert t.exit_price == D(95)
    assert t.r_multiple == D(-1)


def test_a_bar_that_reaches_both_counts_as_the_stop() -> None:
    frame = bars([100, 100, 100, 100], highs=[100, 101, 120, 100], lows=[99, 99, 90, 100])
    (t,) = run(frame).trades
    assert t.exit_reason == "stop"
    assert t.r_multiple == D(-1)


def test_a_gap_through_the_stop_fills_at_the_open() -> None:
    frame = bars([100, 100, 90, 90], highs=[100, 101, 91, 91], lows=[99, 99, 89, 89])
    (t,) = run(frame).trades
    assert t.exit_price == D(90)
    assert t.r_multiple == D(-2)  # a stop limits intent, not the gap


def test_a_gap_through_the_target_fills_at_the_open() -> None:
    frame = bars([100, 100, 120, 120], highs=[100, 101, 121, 121], lows=[99, 99, 119, 119])
    (t,) = run(frame).trades
    assert t.exit_price == D(120)
    assert t.r_multiple == D(4)


def test_the_stop_can_hit_on_the_entry_bar() -> None:
    frame = bars([100, 100, 100], highs=[100, 101, 100], lows=[99, 94, 100])
    (t,) = run(frame).trades
    assert t.entry_time == t.exit_time == frame["open_time"][1]
    assert t.r_multiple == D(-1)


def test_size_is_capped_by_cash_with_no_leverage() -> None:
    # Stop 0.1 below entry: 1% risk would need 100 units (10,000 USDT). Only 10 are affordable.
    frame = bars(
        [100.0] * 4,
        highs=[100.0, 100.5, 100.5, 100.5],
        lows=[99.0, 99.95, 99.95, 99.95],
        step=DAY,
    )
    strategy = FixedBrackets({0: (99.9, 100.3)})
    result = run_backtest(strategy, frame, Interval.D1, config(costs=False), D(1000))
    (t,) = result.trades
    assert t.quantity == D(10)
    assert t.risk_capped
    assert t.risk == D(1)  # 0.1% of equity, not the 1% asked for
    assert compute_metrics(result).risk_capped == 1


def test_risk_per_trade_comes_from_config() -> None:
    frame = bars([100, 100, 100, 100], highs=[100, 101, 101, 101], lows=[99, 99, 99, 99])
    (t,) = run(frame, risk=0.02).trades
    assert t.quantity == D(4)  # 2% of 1,000 / 5


def test_entry_is_skipped_when_the_open_is_past_the_stop() -> None:
    frame = bars([100, 94, 94, 94], highs=[100, 95, 95, 95], lows=[99, 93, 93, 93])
    result = run(frame)
    assert result.trades == []
    assert "outside stop 95" in result.rejections[0].rejection.reason


def test_new_signals_are_ignored_while_a_trade_is_open() -> None:
    entries = {i: (95.0, 115.0) for i in range(5)}
    frame = bars([100] * 6, highs=[101] * 6, lows=[99] * 6)
    result = run(frame, entries)
    assert len(result.trades) == 1  # entered once; closed at the end
    assert result.trades[0].exit_reason == "end of data"


def test_open_trades_are_closed_at_the_final_close() -> None:
    frame = bars(
        [100, 100, 104, 106],
        closes=[100, 104, 106, 108],
        highs=[100, 105, 107, 109],
        lows=[99, 99, 103, 105],
    )
    result = run(frame)
    (t,) = result.trades
    assert t.exit_reason == "end of data"
    assert t.exit_time == frame["open_time"][-1] + timedelta(hours=4)  # the final close
    assert t.exit_price == D(108)
    assert t.r_multiple == D("1.6")  # 8 points gained per unit over 5 points of risk
    assert result.equity["position"][-1] == 0
    assert result.final_equity == D(1016)


def test_a_new_trade_can_follow_an_exit() -> None:
    entries = {0: (95.0, 115.0), 3: (95.0, 115.0)}
    frame = bars([100] * 6, highs=[101, 101, 116, 101, 101, 101], lows=[99] * 6)
    trades = run(frame, entries).trades
    assert [t.exit_reason for t in trades] == ["target", "end of data"]


def test_costs_are_counted_in_r() -> None:
    frame = bars(
        [100, 100, 103, 110],
        closes=[100, 103, 110, 112],
        highs=[100, 104, 111, 116],
        lows=[99, 99, 102, 109],
    )
    t = run_backtest(FixedBrackets(SIGNAL), frame, H4, config(), D(1000)).trades[0]
    assert t.cost_r > 0
    assert t.r_multiple == pytest.approx(D(3) - t.cost_r, abs=D("0.0001"))
    # About 11 bps per side on a 5% stop is roughly 0.05R for the round trip.
    assert D("0.03") < t.cost_r < D("0.08")


def test_the_exchange_minimum_still_applies() -> None:
    frame = bars([100, 100, 100], highs=[100, 101, 100], lows=[99, 99, 100])
    result = run_backtest(FixedBrackets(SIGNAL), frame, H4, config(costs=False), D(100))
    # 1% of 100 = 1 USDT of risk over 5 = 0.2 units = 20 USDT: above the 5 USDT minimum.
    assert len(result.trades) == 1
    tiny = run_backtest(FixedBrackets({0: (50.0, 250.0)}), frame, H4, config(costs=False), D(100))
    assert tiny.trades == []  # 1 / 50 = 0.02 units = 2 USDT: refused, like Binance
    assert "below the minimum" in tiny.rejections[0].rejection.reason


def test_an_exit_the_exchange_refuses_keeps_the_position() -> None:
    # 1% of 1,000 over a 50-point stop is 0.2 units (20 USDT). A gap to 20 makes the
    # exit worth 4 USDT, under Binance's 5 USDT minimum, so the sell is refused.
    frame = bars([100, 100, 20, 20], highs=[100, 101, 21, 21], lows=[99, 99, 19, 19])
    result = run(frame, {0: (50.0, 250.0)})
    assert result.trades == []
    assert any("below the minimum" in r.rejection.reason for r in result.rejections)
    assert result.equity["position"][-1] > 0


def test_r_statistics_and_report() -> None:
    # Three trades: +3R, -1R, -1R. Daily bars so there are enough days for metrics.
    frame = bars(
        [100] * 10,
        step=DAY,
        highs=[100, 116, 100, 100, 100, 100, 100, 100, 100, 100],
        lows=[99, 99, 99, 94, 99, 99, 94, 99, 99, 99],
    )
    entries = {0: (95.0, 115.0), 2: (95.0, 115.0), 5: (95.0, 115.0)}
    result = run_backtest(FixedBrackets(entries), frame, Interval.D1, config(costs=False), D(1000))
    assert [float(t.r_multiple) for t in result.trades] == [3.0, -1.0, -1.0]
    m = compute_metrics(result)
    assert m.round_trips == 3
    assert m.win_rate == pytest.approx(1 / 3)
    assert m.avg_win_r == 3.0
    assert m.avg_loss_r == -1.0
    assert m.expectancy_r == pytest.approx(1 / 3)
    assert m.profit_factor == 1.5
    assert m.avg_cost_r == 0.0
    report = format_report(result, m)
    assert "Expectancy        +0.33R per trade after costs" in report
    assert "too few to trust" in report


def test_weight_strategies_have_no_r_statistics() -> None:
    from kestrel.strategy import BuyAndHold

    m = compute_metrics(run_backtest(BuyAndHold(), bars([100.0] * 20), H4, config(), D(1000)))
    assert m.round_trips is None
    assert m.expectancy_r is None


def test_a_bracket_strategy_with_no_trades_reports_zero_round_trips() -> None:
    frame = bars([100.0] * 20, step=DAY)
    m = compute_metrics(run_backtest(FixedBrackets({}), frame, Interval.D1, config(), D(1000)))
    assert m.round_trips == 0
    assert m.expectancy_r is None


class PeeksBracket:
    name = "peeks"
    symbol = "BTCUSDT"

    def brackets(self, frame: pl.DataFrame) -> pl.DataFrame:
        enter = (frame["close"].shift(-1) > frame["close"]).fill_null(False)
        return pl.DataFrame(
            {"enter": enter, "stop": frame["close"] * 0.9, "target": frame["close"] * 1.2}
        )


def test_brackets_are_checked_for_lookahead() -> None:
    frame = bars([float(100 + (i % 3)) for i in range(40)])
    with pytest.raises(LookaheadError, match="information from the future"):
        validated_brackets(PeeksBracket(), frame)


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ({0: (115.0, 95.0)}, "stop < target if set"),
        ({0: (0.0, 95.0)}, "every entry needs 0 < stop"),
    ],
)
def test_bad_levels_are_refused(entries: dict[int, tuple[float, float]], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validated_brackets(FixedBrackets(entries), bars([100.0] * 4))


def test_bracket_shape_is_checked() -> None:
    class Wrong:
        name = "wrong"
        symbol = "BTCUSDT"

        def __init__(self, frame: pl.DataFrame) -> None:
            self.frame = frame

        def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
            return self.frame

    with pytest.raises(ValueError, match="bracket rows"):
        validated_brackets(Wrong(pl.DataFrame({"enter": [False]})), bars([100.0] * 3))
    with pytest.raises(ValueError, match="bracket columns"):
        validated_brackets(Wrong(pl.DataFrame({"enter": [False] * 3})), bars([100.0] * 3))


def test_channel_breakout_enters_on_a_new_high_with_a_stop_at_the_recent_low() -> None:
    closes = [100.0, 101, 100, 102, 101, 105]
    frame = bars(
        closes, closes=closes, highs=[c + 0.5 for c in closes], lows=[c - 1 for c in closes]
    )
    b = validated_brackets(ChannelBreakout(lookback=3, stop_lookback=2, target_r=2), frame)
    assert b["enter"].to_list() == [False, False, False, True, False, True]
    # Bar 3: close 102 > prior 3-bar high 101.5; stop = min(low[2], low[3]) = 99; target 102 + 2*3.
    assert b["stop"][3] == 99.0
    assert b["target"][3] == 108.0
    assert b["stop"][4] is None


def test_channel_breakout_has_no_lookahead_and_trades() -> None:
    import math

    closes = [100 + 10 * math.sin(i / 7) + i * 0.05 for i in range(400)]
    frame = bars(
        closes, closes=closes, highs=[c * 1.004 for c in closes], lows=[c * 0.996 for c in closes]
    )
    result = run_backtest(
        ChannelBreakout(lookback=20, stop_lookback=10, target_r=2), frame, H4, config(), D(1000)
    )
    assert len(result.trades) >= 3
    assert {t.exit_reason for t in result.trades} <= {"target", "stop", "end of data"}


def test_an_exit_signal_sells_at_the_next_open() -> None:
    # Bought at bar 1's open (100); bar 2's close signals an exit, so it sells at bar 3's
    # open (104), never touching the stop or the target.
    frame = bars([100, 100, 103, 104, 104], closes=[100, 103, 104, 104, 104])
    strategy = FixedBrackets({0: (95.0, 115.0)}, exits={2})
    (t,) = run_backtest(strategy, frame, H4, config(costs=False), D(1000)).trades
    assert t.exit_reason == "signal"
    assert t.exit_time == frame["open_time"][3]
    assert t.exit_price == D(104)
    assert t.r_multiple == D("0.8")  # 4 gained per unit over 5 of risk


def test_an_exit_signal_with_no_open_trade_does_nothing() -> None:
    frame = bars([100.0] * 5)
    result = run_backtest(FixedBrackets({}, exits={1, 2}), frame, H4, config(costs=False), D(1000))
    assert result.trades == []
    assert result.fills == []


def test_a_trade_without_a_target_rides_until_its_stop() -> None:
    frame = bars(
        [100, 100, 130, 140, 90], highs=[100, 101, 131, 141, 100], lows=[99, 99, 129, 139, 89]
    )
    (t,) = run_backtest(
        FixedBrackets({0: (95.0, None)}), frame, H4, config(costs=False), D(1000)
    ).trades
    assert t.target is None
    assert t.exit_reason == "stop"  # a 3R move to 115 and beyond didn't end it
    assert t.exit_price == D(90)  # bar 4 gapped through the stop


def test_without_a_target_only_the_stop_must_be_below_the_open() -> None:
    frame = bars([100, 94, 94], highs=[100, 95, 95], lows=[99, 93, 93])
    result = run_backtest(FixedBrackets({0: (95.0, None)}), frame, H4, config(costs=False), D(1000))
    assert result.trades == []
    assert "at or below stop 95" in result.rejections[0].rejection.reason


def test_a_failed_exit_keeps_the_trade_and_records_why() -> None:
    # 1% of 1,000 over 99 of risk per unit buys about 0.101 units (about 10 USDT). The
    # price then falls to 40 without reaching the stop at 1, so the signalled exit is
    # worth about 4 USDT: below the 5 USDT exchange minimum, and refused.
    frame = bars([100, 100, 40, 40], closes=[100, 40, 40, 40])
    result = run_backtest(
        FixedBrackets({0: (1.0, None)}, exits={2}), frame, H4, config(costs=False), D(1000)
    )
    assert [r.fill.side.value for r in result.fills] == ["buy"]
    assert result.rejections[0].time == frame["open_time"][3]
    assert "below the minimum" in result.rejections[0].rejection.reason
    assert result.trades == []  # never closed: kept, not silently dropped
