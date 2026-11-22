"""The decay monitor (kestrel.validation.decay): the rule, its bootstrap, its backtest and
its alert, offline."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from kestrel.config import load_config
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.execution import alerts
from kestrel.execution.paper import Terms
from kestrel.execution.tracking import DayTrack, Tracking
from kestrel.validation import decay
from tests.backtest.helpers import bars
from tests.test_alerts import account

REPO_CONFIG = Path(__file__).resolve().parent.parent / "kestrel.toml"


def returns(n: int, mean: float, sd: float, seed: int = 1) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_sharpe_is_annualised_and_zero_when_nothing_moved() -> None:
    assert decay.sharpe([0.0] * 50) == 0.0
    assert decay.sharpe([0.01]) == 0.0
    r = [0.01, -0.005, 0.02, 0.0]
    mean = sum(r) / 4
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / 3)
    assert decay.sharpe(r) == pytest.approx(mean / sd * math.sqrt(365))


def test_the_band_is_reproducible_and_tightens_with_more_days() -> None:
    ref = returns(2000, 0.002, 0.03)  # a Sharpe near 1.3
    floor90, median90 = decay.sharpe_band(ref, 90)
    assert decay.sharpe_band(ref, 90) == (floor90, median90)  # seeded
    floor395, median395 = decay.sharpe_band(ref, 395)
    assert floor90 < floor395 < median395  # a longer run is held to a tighter floor
    assert median395 == pytest.approx(decay.sharpe(ref), abs=0.3)
    with pytest.raises(ValueError, match="at least 2"):
        decay.sharpe_band([0.01], 90)


def test_too_early_never_runs_the_backtest() -> None:
    def boom() -> list[float]:
        raise AssertionError("the reference ran before it was due")

    d = decay.assess([0.0] * (decay.MIN_DAYS - 1), boom)
    assert (d.status, d.days, d.sharpe) == ("too early", decay.MIN_DAYS - 1, None)
    assert "too early (89 of 90 days)" in decay.format_decay(d)


def test_it_flags_a_run_far_below_its_backtest_and_passes_a_typical_one() -> None:
    ref = returns(2000, 0.002, 0.03)
    bad = decay.assess(returns(120, -0.01, 0.03, seed=2), lambda: ref)
    assert bad.status == "flagged"
    assert "FLAGGED" in decay.format_decay(bad)
    typical = decay.assess(returns(120, 0.002, 0.03, seed=5), lambda: ref)  # Sharpe 1.7
    assert typical.status == "in line"
    assert typical.floor is not None
    assert typical.median is not None
    assert typical.floor < typical.median


def test_model_returns_come_from_the_replay_not_the_account() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    day = timedelta(days=1)
    z = Decimal(0)
    days = [
        DayTrack(t0 + i * day, Decimal(999), Decimal(e), z, z, False)
        for i, e in enumerate(["100", "110", "99"])
    ]
    assert decay.model_returns(Tracking(days, [])) == pytest.approx([0.1, -0.1])


def test_the_reference_backtest_stops_before_the_holdout(tmp_path: Path) -> None:
    cfg = load_config(REPO_CONFIG)
    start = datetime(2025, 8, 1, tzinfo=UTC)
    n = 200  # runs well into the holdout
    ParquetStore(tmp_path).write(
        "BTCUSDT",
        Interval.D1,
        bars([100.0 + i for i in range(n)], start=start, step=timedelta(days=1)),
    )
    terms = Terms(
        "b", "ma-trend", "BTCUSDT", Interval.D1, {"fast": 5, "slow": 20}, Decimal(330), start
    )
    found = decay.reference_returns(terms, tmp_path, cfg)
    before = (cfg.backtest.holdout_start - start).days  # bars before the holdout
    assert 0 < len(found) <= before < n  # daily returns, none from the holdout


def test_a_flag_is_alerted_once_and_a_good_run_never(tmp_path: Path) -> None:
    acct, now = account(tmp_path)
    flagged = decay.Decay(120, -3.1, -2.2, 1.1)
    fine = decay.Decay(120, 0.5, -2.2, 1.1)
    assert not [a for a in alerts.found(acct, now, decay=fine) if a.kind == "decay"]
    sent: list[alerts.Alert] = []
    alerts.review(acct, now, sent.append)  # the first review records history
    (alert,) = alerts.review(acct, now, sent.append, decay=flagged)
    assert alert.kind == "decay"
    assert "-3.10 over 120 days" in alert.message
    assert "nothing changes without Luke" in alert.message
    assert alerts.review(acct, now, sent.append, decay=flagged) == []  # once per account
