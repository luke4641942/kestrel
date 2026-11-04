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


def test_moving_average_trend_is_long_only_when_fast_is_above_slow() -> None:
    from kestrel.strategy import MovingAverageTrend

    rising_then_falling = [100.0 + i for i in range(10)] + [110.0 - 3 * i for i in range(10)]
    w = validated_weights(MovingAverageTrend(fast=2, slow=4), bars(rising_then_falling))
    assert w[:3].to_list() == [0.0, 0.0, 0.0]  # slow window not full yet: cash
    assert w[5] == 1.0  # uptrend
    assert w[-1] == 0.0  # downtrend


def test_moving_average_trend_has_no_lookahead() -> None:
    from kestrel.strategy import MovingAverageTrend

    noisy = [100.0 + ((i * 7) % 11) - 5 + i * 0.3 for i in range(300)]
    validated_weights(MovingAverageTrend(fast=5, slow=30), bars(noisy))  # raises if it peeks


def test_params_are_filled_and_checked() -> None:
    from kestrel.strategy import make_strategy, resolve_params

    assert resolve_params("ma-trend") == {"fast": 20, "slow": 100}
    assert resolve_params("ma-trend", {"fast": 10}) == {"fast": 10, "slow": 100}
    assert resolve_params("buy-and-hold") == {}
    assert make_strategy("ma-trend", "btcusdt", {"fast": 5, "slow": 50}).name == (
        "moving-average trend 5/50 BTCUSDT"
    )


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"window": 5}, "no parameter"),
        ({"fast": 1}, "must be 2 to 200"),
        ({"fast": 1.5}, "whole number"),
        ({"fast": True}, "whole number"),
    ],
)
def test_bad_params_are_refused(params: dict[str, object], message: str) -> None:
    from kestrel.strategy import resolve_params

    with pytest.raises(ValueError, match=message):
        resolve_params("ma-trend", params)  # type: ignore[arg-type]


def test_fast_must_be_shorter_than_slow() -> None:
    from kestrel.strategy import make_strategy

    with pytest.raises(ValueError, match="fast < slow"):
        make_strategy("ma-trend", "BTCUSDT", {"fast": 50, "slow": 50})


def test_unknown_strategy_key() -> None:
    from kestrel.strategy import spec_for

    with pytest.raises(ValueError, match="unknown strategy"):
        spec_for("magic")


def test_source_is_the_real_implementation() -> None:
    from kestrel.strategy import strategy_source

    source = strategy_source("ma-trend")
    assert source.startswith("class MovingAverageTrend")
    assert "rolling_mean" in source
