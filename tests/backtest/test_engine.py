from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.engine import HoldoutError, run_backtest
from kestrel.data.schema import Interval
from kestrel.strategy import BuyAndHold, LookaheadError
from tests.backtest.helpers import FixedWeights, bars, config

D = Decimal
H4 = Interval.H4


def test_decision_at_close_fills_at_next_open() -> None:
    # Weight turns on at bar 2's close, so the buy fills at bar 3's open (103), not 102.
    frame = bars([100, 101, 102, 103, 104])
    result = run_backtest(FixedWeights([0, 0, 1, 1, 1]), frame, H4, config(costs=False), D(1000))
    assert len(result.fills) == 1
    rec = result.fills[0]
    assert rec.time == frame["open_time"][3]
    assert rec.fill.reference_price == D(103)


def test_zero_cost_buy_and_hold_is_exact() -> None:
    # Buy at bar 1's open (100) with 1000 USDT -> 10 BTC; marked at the last close (150).
    frame = bars([90, 100, 120, 140], closes=[95, 110, 130, 150])
    result = run_backtest(BuyAndHold(), frame, H4, config(costs=False), D(1000))
    assert result.fills[0].fill.quantity == D(10)
    assert result.equity["equity"].to_list() == [1000, 1100, 1300, 1500]
    assert result.equity["weight"][-1] == pytest.approx(1.0)


def test_costs_are_paid_and_cash_never_goes_negative() -> None:
    frame = bars([100, 100, 100, 100])
    result = run_backtest(BuyAndHold(), frame, H4, config(), D(1000))
    fill = result.fills[0].fill
    assert fill.total_cost > 0
    # Everything not spent on BTC or costs is still cash, and nothing was overspent.
    assert result.equity["cash"].min() >= 0
    assert result.final_equity == pytest.approx(D(1000) - fill.total_cost, abs=D("1e-9"))


def test_selling_to_zero_closes_the_position() -> None:
    frame = bars([100, 100, 110, 120, 130])
    result = run_backtest(FixedWeights([1, 1, 0, 0, 0]), frame, H4, config(costs=False), D(1000))
    assert [r.fill.side.value for r in result.fills] == ["buy", "sell"]
    assert result.equity["position"][-1] == 0
    assert result.final_equity == D(1200)  # 10 BTC bought at 100, sold at 120's open


def test_no_trade_band_skips_small_changes() -> None:
    weights = [0.5, 0.51, 0.5, 0.51, 0.5, 0.51]
    frame = bars([100] * 6)
    result = run_backtest(FixedWeights(weights), frame, H4, config(costs=False), D(1000))
    # Decisions after the first buy: 0.51, 0.50, 0.51, 0.50 against a held 0.50. The two
    # 0.51s are inside the band and skipped; the 0.50s already match, so nothing to skip.
    assert len(result.fills) == 1
    assert result.band_skips == 2


def test_price_drift_is_not_counted_as_a_band_skip() -> None:
    # Regression: buy-and-hold's weight drifts a hair below 1 every bar as prices move;
    # that is not a strategy decision, so the band skipped nothing.
    frame = bars([100.0 + (i % 7) for i in range(50)])
    result = run_backtest(BuyAndHold(), frame, H4, config(), D(1000))
    assert result.band_skips == 0


def test_band_of_zero_trades_every_change() -> None:
    weights = [0.5, 0.6, 0.5, 0.6]
    frame = bars([100] * 4)
    result = run_backtest(FixedWeights(weights), frame, H4, config(costs=False, band=0), D(1000))
    assert len(result.fills) == 3
    assert result.band_skips == 0


def test_rejections_are_recorded_not_hidden() -> None:
    # 3 USDT can't meet Binance's 5 USDT minimum.
    result = run_backtest(BuyAndHold(), bars([100] * 4), H4, config(), D(3))
    assert not result.fills
    assert result.rejections
    assert "below the minimum 5" in result.rejections[0].rejection.reason
    assert result.final_equity == D(3)


def test_holdout_is_refused_unless_allowed() -> None:
    late = bars([100] * 4, start=datetime(2025, 10, 31, 16, tzinfo=UTC))
    with pytest.raises(HoldoutError, match="2025-11-01"):
        run_backtest(BuyAndHold(), late, H4, config(), D(1000))
    result = run_backtest(BuyAndHold(), late, H4, config(), D(1000), allow_holdout=True)
    assert result.used_holdout


def test_research_before_holdout_is_unflagged() -> None:
    result = run_backtest(BuyAndHold(), bars([100] * 4), H4, config(), D(1000))
    assert not result.used_holdout


class PeeksAhead:
    """Goes long when the *next* close is higher: the classic lookahead bug."""

    name = "peeks ahead"
    symbol = "BTCUSDT"

    def target_weights(self, frame: pl.DataFrame) -> pl.Series:
        return (frame["close"].shift(-1) > frame["close"]).fill_null(False).cast(pl.Float64)


def test_lookahead_strategy_is_refused() -> None:
    frame = bars([float(100 + (i % 3)) for i in range(40)])
    with pytest.raises(LookaheadError, match="using information from the future"):
        run_backtest(PeeksAhead(), frame, H4, config(), D(1000))


@pytest.mark.parametrize(
    ("weights", "message"),
    [([1.5, 1, 1, 1], r"\[0, 1\]"), ([-0.1, 0, 0, 0], r"\[0, 1\]"), ([1, 1], "2 weights for 4")],
)
def test_invalid_weights_are_refused(weights: list[float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        run_backtest(FixedWeights(weights), bars([100] * 4), H4, config(), D(1000))


def test_unsorted_bars_are_refused() -> None:
    frame = bars([100] * 4).reverse()
    with pytest.raises(ValueError, match="sorted"):
        run_backtest(BuyAndHold(), frame, H4, config(), D(1000))


def test_equity_close_times_are_bar_closes() -> None:
    frame = bars([100] * 3)
    result = run_backtest(BuyAndHold(), frame, H4, config(), D(1000))
    assert result.equity["close_time"][0] == frame["open_time"][0] + timedelta(hours=4)


def test_result_records_provenance() -> None:
    result = run_backtest(BuyAndHold(), bars([100] * 3), H4, config(), D(1000))
    assert len(result.config_fingerprint) == 12
    assert result.code is not None
