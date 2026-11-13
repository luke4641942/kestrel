"""Short-term reversal after big down days, on hand-built daily closes. Synthetic bars
only: the strategy must not be run on market data until its research note is frozen."""

from __future__ import annotations

import math
from datetime import timedelta

from kestrel.strategies.reversal import DropReversal
from kestrel.strategy import STRATEGIES, make_strategy, validated_weights
from tests.backtest.helpers import bars
from tests.chart import random_walk

CALM = [0.01, -0.01] * 15
"""30 daily log returns with a standard deviation of about 1%."""


def daily(log_returns: list[float]):  # type: ignore[no-untyped-def]
    closes, price = [100.0], 100.0
    for r in log_returns:
        price *= math.exp(r)
        closes.append(price)
    return bars([closes[0], *closes[:-1]], closes=closes, step=timedelta(days=1))


def weights(log_returns: list[float], **params: int) -> list[float]:
    return validated_weights(DropReversal(**params), daily(log_returns)).to_list()


def test_holds_for_three_days_after_a_big_drop() -> None:
    w = weights([*CALM, -0.05, 0.0, 0.0, 0.0, 0.0])
    # Bar 31 closes the drop: held at its close and the next two, then cash.
    assert w[:31] == [0.0] * 31
    assert w[31:] == [1.0, 1.0, 1.0, 0.0, 0.0]


def test_a_drop_inside_normal_volatility_is_ignored() -> None:
    assert sum(weights([*CALM, -0.015, 0.0, 0.0])) == 0


def test_the_drop_is_measured_against_volatility_before_it() -> None:
    # Against the calm days before it, a 2.1% drop is just over 2 standard deviations (2.03%).
    # In a window that included it, it would widen its own yardstick to 2.14% and not count.
    assert weights([*CALM, -0.021])[-1] == 1.0


def test_cash_until_the_volatility_window_has_filled() -> None:
    assert weights([0.01, -0.01, -0.2, 0.0]) == [0.0] * 5


def test_another_drop_while_holding_restarts_the_clock() -> None:
    w = weights([*CALM, -0.05, 0.0, -0.06, 0.0, 0.0, 0.0])
    assert w[31:] == [1.0, 1.0, 1.0, 1.0, 1.0, 0.0]


def test_settings_are_parameters() -> None:
    returns = [*CALM, -0.015, 0.0, 0.0, 0.0]
    assert weights(returns, drop_sigma_pct=120, hold_days=2)[31:] == [1.0, 1.0, 0.0, 0.0]


def test_no_lookahead_on_a_random_walk() -> None:
    w = validated_weights(make_strategy("drop-reversal", "BTCUSDT"), random_walk())  # type: ignore[arg-type]
    assert 0 < w.sum() < len(w)


def test_registered_as_research() -> None:
    spec = STRATEGIES["drop-reversal"]
    assert spec.kind == "research"
    assert [p.default for p in spec.params] == [200, 30, 3]
