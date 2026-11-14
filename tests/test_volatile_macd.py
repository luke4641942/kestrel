"""Riding volatile spells in MACD's direction: week-long holds, and the daily switch
(hold_days 1), on synthetic bars with the regime feature set by hand."""

from __future__ import annotations

import random
from datetime import timedelta

import polars as pl
import pytest

from kestrel.data.features import REGIME_VOLATILE
from kestrel.indicators import macd
from kestrel.strategies.volatile_macd import VolatileMacd
from kestrel.strategy import STRATEGIES, make_strategy, validated_weights
from tests.test_volhold_weekly import daily


def with_flags(frame: pl.DataFrame, flags: list[float | None]) -> pl.DataFrame:
    return frame.with_columns(pl.Series(REGIME_VOLATILE, flags, dtype=pl.Float64))


def rising_then_falling() -> pl.DataFrame:
    # 60 days up (MACD histogram turns positive), then 60 down (it turns negative).
    return daily([0.02] * 40 + [0.03] * 20 + [-0.03] * 60)


def test_a_signal_holds_for_a_week_then_decides_again() -> None:
    frame = rising_then_falling()
    hist = macd(frame["close"]).histogram.to_list()
    first_up = next(i for i, h in enumerate(hist) if h is not None and h > 0)
    flags: list[float | None] = [0.0] * frame.height
    flags[first_up] = 1.0  # one volatile day, with MACD up
    w = VolatileMacd().target_weights(with_flags(frame, flags)).to_list()
    held = [i for i, x in enumerate(w) if x == 1.0]
    assert held == list(range(first_up, first_up + 7))  # exactly one week, then cash


def test_it_stays_in_while_the_conditions_hold_and_ignores_signals_mid_week() -> None:
    frame = rising_then_falling()
    hist = macd(frame["close"]).histogram.to_list()
    up = [i for i, h in enumerate(hist) if h is not None and h > 0]
    start, end = up[0], up[0] + 20
    flags = [1.0 if start <= i <= end else 0.0 for i in range(frame.height)]
    w = VolatileMacd().target_weights(with_flags(frame, flags)).to_list()
    # Decided at start, start+7, start+14 (all volatile and up): one unbroken hold to
    # start+20, the third week's end; the signal at start+15..20 changed nothing mid-week.
    assert [i for i, x in enumerate(w) if x == 1.0] == list(range(start, start + 21))


def test_the_daily_switch_is_a_one_day_hold() -> None:
    frame = rising_then_falling()
    rnd = random.Random(1)
    flags: list[float | None] = [float(rnd.random() < 0.5) for _ in range(frame.height)]
    hist = macd(frame["close"]).histogram.to_list()
    w = VolatileMacd(hold_days=1).target_weights(with_flags(frame, flags)).to_list()
    expected = [
        1.0 if v == 1.0 and h is not None and h > 0 else 0.0
        for v, h in zip(flags, hist, strict=True)
    ]
    assert w == expected
    assert sum(w) > 0


def test_no_regime_state_and_down_signals_mean_cash() -> None:
    frame = rising_then_falling()
    assert VolatileMacd().target_weights(with_flags(frame, [None] * frame.height)).sum() == 0
    hist = macd(frame["close"]).histogram.to_list()
    downs = [1.0 if h is not None and h < 0 else 0.0 for h in hist]  # volatile only when down
    assert VolatileMacd().target_weights(with_flags(frame, downs)).sum() == 0


def test_misuse() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        VolatileMacd(hold_days=0)
    with pytest.raises(ValueError, match="needs the regime_volatile feature"):
        VolatileMacd().target_weights(daily([0.01] * 30))
    with pytest.raises(ValueError, match="daily bars"):
        VolatileMacd().target_weights(
            with_flags(daily([0.01] * 30, step=timedelta(hours=4)), [1.0] * 31)
        )
    assert VolatileMacd().target_weights(daily([])).to_list() == [0.0]
    spec = STRATEGIES["volatile-macd"]
    assert (spec.kind, spec.features, spec.params[0].default) == (
        "research",
        ("regime_volatile",),
        7,
    )


@pytest.mark.parametrize("hold", [1, 7])
def test_no_lookahead(hold: int) -> None:
    rnd = random.Random(9)
    frame = daily([rnd.gauss(0.001, 0.04) for _ in range(500)])
    frame = with_flags(frame, [float(rnd.random() < 0.3) for _ in range(frame.height)])
    strategy = make_strategy("volatile-macd", "BTCUSDT", {"hold_days": hold})
    w = validated_weights(strategy, frame)  # type: ignore[arg-type]
    assert 0 < w.sum() < len(w)
