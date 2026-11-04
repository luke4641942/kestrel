from __future__ import annotations

import polars as pl
import pytest

from kestrel.strategy import BuyAndHold, LookaheadError, validated_weights
from tests.backtest.helpers import FixedWeights, bars


def test_buy_and_hold_is_always_fully_invested() -> None:
    weights = validated_weights(BuyAndHold(), bars([100.0] * 5))
    assert weights.to_list() == [1.0] * 5


def test_nan_weights_are_refused() -> None:
    with pytest.raises(ValueError, match="nulls or NaN"):
        validated_weights(FixedWeights([0.5, float("nan"), 0.5]), bars([100.0] * 3))


def test_a_single_bar_has_nothing_to_check() -> None:
    assert validated_weights(BuyAndHold(), bars([100.0])).to_list() == [1.0]


class NormalisesByFullSample:
    """Scales by the whole history's max price: subtle lookahead, no shift(-1) in sight."""

    name = "full-sample normalisation"
    symbol = "BTCUSDT"

    def target_weights(self, frame: pl.DataFrame) -> pl.Series:
        return (frame["close"] / frame["close"].max()).alias("weight")


def test_subtle_full_sample_lookahead_is_caught() -> None:
    rising = bars([100.0 + i for i in range(30)])
    with pytest.raises(LookaheadError, match="bar"):
        validated_weights(NormalisesByFullSample(), rising)


class IgnoresHistoryLength:
    """Returns a fixed-length answer whatever it is given."""

    name = "fixed length"
    symbol = "BTCUSDT"

    def target_weights(self, frame: pl.DataFrame) -> pl.Series:
        return pl.Series("weight", [1.0] * 20)


def test_wrong_length_on_a_prefix_is_caught() -> None:
    with pytest.raises(LookaheadError):
        validated_weights(IgnoresHistoryLength(), bars([100.0] * 20))
