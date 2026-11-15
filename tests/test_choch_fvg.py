"""CHoCH + FVG at the New York open: where the limit goes, when it is placed, and that it
never looks ahead. Hand-built 5m bars with one-bar swings (swing_bars=1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from kestrel.strategies import choch_fvg
from kestrel.strategies.choch_fvg import ChochFvg
from kestrel.strategy import make_strategy, validated_brackets
from tests.backtest.helpers import bars

FIVE = timedelta(minutes=5)
NY = datetime(2024, 3, 1, 13, 0, tzinfo=UTC)
FLAT = 16  # flat bars before the pattern, so ATR(14) is known


def pattern(rows: list[tuple[float, float, float]], start: datetime = NY) -> pl.DataFrame:
    """FLAT quiet bars, then ``rows`` of (high, low, close) from ``start``."""
    quiet = [(10.0, 9.9, 9.95)] * FLAT
    highs, lows, closes = zip(*(quiet + rows), strict=True)
    opens = [closes[0], *closes[:-1]]
    return bars(
        list(opens),
        closes=list(closes),
        highs=list(highs),
        lows=list(lows),
        start=start - FLAT * FIVE,
        step=FIVE,
    )


# Swing high A (12) at row 1, lower swing high B (11.5) at row 3, the leg's low (7) at
# row 4, and row 7 closes above B: a change of character. Row 7 completes a gap over
# row 5 (low 11 > high 9): limit at its midpoint 10, stop at row 5's low 8.
GAP_ON_BREAK = [
    (10.0, 9.0, 9.5),
    (12.0, 10.0, 11.0),
    (11.0, 9.0, 10.0),
    (11.5, 9.0, 10.0),
    (9.5, 7.0, 8.0),
    (9.0, 8.0, 8.5),
    (10.0, 9.0, 9.8),
    (12.0, 11.0, 11.8),
    (12.2, 11.5, 12.0),
]


def strategy(**over: int) -> ChochFvg:
    settings = {"swing_bars": 1, "min_gap_atr_tenths": 1}
    settings.update(over)
    return ChochFvg("SOLUSDT", **settings)


def test_a_limit_goes_at_the_gaps_midpoint_with_the_stop_below_it() -> None:
    frame = strategy(target_r=4).brackets(pattern(GAP_ON_BREAK))
    orders = frame.with_row_index().filter(pl.col("enter"))
    assert orders["index"].to_list() == [FLAT + 7]  # placed at the break's close
    assert orders["limit"][0] == pytest.approx(10.0)
    assert orders["stop"][0] == pytest.approx(8.0)
    assert orders["target"][0] == pytest.approx(10.0 + 4 * 2.0)
    assert orders["valid_bars"][0] == 6


def test_outside_the_new_york_open_there_is_no_order() -> None:
    early = pattern(GAP_ON_BREAK, start=NY - timedelta(hours=5))
    assert not strategy().brackets(early)["enter"].any()


def test_a_gap_completed_by_the_next_bar_places_the_order_there() -> None:
    rows = [*GAP_ON_BREAK[:7]]
    rows[6] = (10.0, 9.0, 9.8)  # row 6: low 9 is not above row 4's high 9.5
    rows.append((12.0, 8.9, 11.8))  # row 7 breaks B, but low 8.9 leaves no gap over row 5
    rows.append((13.0, 10.5, 12.5))  # row 8: low 10.5 above row 6's high 10, a gap
    frame = strategy().brackets(pattern(rows))
    orders = frame.with_row_index().filter(pl.col("enter"))
    assert orders["index"].to_list() == [FLAT + 8]
    assert orders["limit"][0] == pytest.approx((10.0 + 10.5) / 2)
    assert orders["stop"][0] == pytest.approx(9.0)


def test_a_gap_smaller_than_the_atr_filter_is_no_order() -> None:
    assert not strategy(min_gap_atr_tenths=60).brackets(pattern(GAP_ON_BREAK))["enter"].any()


def test_the_daily_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(choch_fvg, "MAX_PER_DAY", 0)
    assert not strategy().brackets(pattern(GAP_ON_BREAK))["enter"].any()


def test_no_lookahead_and_the_registry_builds_it() -> None:
    frame = pattern(GAP_ON_BREAK + [(12.0, 11.8, 11.9)] * 20)
    checked = validated_brackets(strategy(), frame, checkpoints=30)  # raises on lookahead
    assert checked["enter"].sum() == 1
    built = make_strategy("choch-fvg", "SOLUSDT", {"expiry_bars": 4})
    assert isinstance(built, ChochFvg)
    assert built.expiry_bars == 4
