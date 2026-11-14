"""The regime filter on trend-vol-target: the feature from a stored fit, and the filter."""

from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from kestrel import regimes as rg
from kestrel.data.features import REGIME_VOLATILE, attach_features
from kestrel.data.schema import Interval
from kestrel.strategies.regime import TrendVolTargetRegime
from kestrel.strategies.trend import TrendVolTarget
from kestrel.strategy import STRATEGIES, make_strategy, validated_weights
from tests.test_volhold_weekly import daily

D1 = Interval.D1


def store_fit(tmp_path: Path, frame: pl.DataFrame, states: list[int | None]) -> None:
    """A stored 3-state fit whose most likely state each day is ``states``."""
    probs: list[list[float] | None] = [
        None if s is None else [0.8 if k == s else 0.1 for k in range(3)] for s in states
    ]
    model = rg.Hmm(np.full(3, 1 / 3), np.full((3, 3), 1 / 3), np.zeros((3, 2)), np.ones((3, 2)))
    times = frame["open_time"].to_list()
    first = next(t for t, s in zip(times, states, strict=True) if s is not None)
    fit = rg.Regimes(times, probs, [rg.Refit(first, model, 0.0)])
    rg.RegimeStore(tmp_path).save("BTCUSDT", fit, frame)


def test_the_feature_marks_the_most_volatile_state(tmp_path: Path) -> None:
    frame = daily([0.01] * 9)  # 10 days
    store_fit(tmp_path, frame, [None, None, 0, 1, 2, 2, 1, 0, 2, 0])
    out = attach_features(frame, ("regime_volatile",), "btcusdt", D1, tmp_path)
    assert out[REGIME_VOLATILE].to_list() == [None, None, 0, 0, 1, 1, 0, 0, 1, 0]
    empty = attach_features(frame.clear(), ("regime_volatile",), "BTCUSDT", D1, tmp_path)
    assert empty.is_empty()


def test_the_feature_refuses_what_the_fit_cant_cover(tmp_path: Path) -> None:
    frame = daily([0.01] * 9)
    with pytest.raises(ValueError, match="no regime fit for BTCUSDT"):
        attach_features(frame, ("regime_volatile",), "BTCUSDT", D1, tmp_path)
    store_fit(tmp_path, frame.head(5), [None, 0, 1, 2, 0])
    with pytest.raises(ValueError, match="Refit before using regimes on later bars"):
        attach_features(frame, ("regime_volatile",), "BTCUSDT", D1, tmp_path)
    with pytest.raises(ValueError, match="daily bars"):
        attach_features(frame, ("regime_volatile",), "BTCUSDT", Interval.H4, tmp_path)


def regime_frame(n: int = 300, seed: int = 2) -> pl.DataFrame:
    rnd = random.Random(seed)
    frame = daily([rnd.gauss(0.002, 0.03) for _ in range(n)])
    flags = [None] * 150 + [float(rnd.random() < 0.2) for _ in range(n + 1 - 150)]
    return frame.with_columns(pl.Series(REGIME_VOLATILE, flags, dtype=pl.Float64))


def test_out_on_volatile_days_otherwise_the_plain_rule() -> None:
    frame = regime_frame()
    plain = TrendVolTarget().target_weights(frame).to_list()
    filtered = TrendVolTargetRegime().target_weights(frame).to_list()
    flags = frame[REGIME_VOLATILE].to_list()
    for p, f, v in zip(plain, filtered, flags, strict=True):
        assert f == (0.0 if v == 1.0 else p)  # null (no state yet) keeps the plain weight
    assert any(v == 1.0 and p > 0 for p, v in zip(plain, flags, strict=True))  # it bites
    off = TrendVolTargetRegime(regime_filter=0).target_weights(frame).to_list()
    assert off == plain
    assert TrendVolTargetRegime(regime_filter=0).target_weights(daily([0.01] * 9)).sum() == 0


def test_misuse() -> None:
    with pytest.raises(ValueError, match="needs the regime_volatile feature"):
        TrendVolTargetRegime().target_weights(daily([0.01] * 30))
    with pytest.raises(ValueError, match="1 \\(on\\) or 0 \\(off\\)"):
        TrendVolTargetRegime(regime_filter=2)
    spec = STRATEGIES["trend-vol-target-regime"]
    assert (spec.kind, spec.features) == ("research", ("regime_volatile",))


def test_no_lookahead() -> None:
    frame = regime_frame(600, seed=5)
    w = validated_weights(make_strategy("trend-vol-target-regime", "BTCUSDT"), frame)  # type: ignore[arg-type]
    assert 0 < w.sum() < len(w)
    assert frame["open_time"].diff().drop_nulls().max() == timedelta(days=1)
