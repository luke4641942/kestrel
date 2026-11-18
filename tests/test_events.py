"""Event studies: abnormal returns against the other coins, a placebo, and the perp-listing
event filters, on synthetic coins with a planted effect (or none)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from kestrel.studies import events as ev
from kestrel.studies.shorting import Grid

START = date(2020, 1, 1)


def coins(n_coins: int, n_days: int, seed: int = 1, noise: float = 0.02) -> Grid:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, noise, size=(n_days, n_coins))
    opens = 100 * np.exp(np.cumsum(r, axis=0))
    shape = opens.shape
    return Grid(
        days=[START + timedelta(days=i) for i in range(n_days)],
        symbols=[f"C{j:02d}USDT" for j in range(n_coins)],
        open=opens, high=opens, close=opens,
        band=np.full(shape, 1e-4), funding=np.zeros(shape), volume=np.full(shape, 1e9),
        age=np.tile(np.arange(1, n_days + 1, dtype=float)[:, None], (1, n_coins)),
        perp=np.zeros(shape, dtype=bool),
    )  # fmt: skip


def plant(g: Grid, j: int, i: int, drop: float) -> None:
    """Coin j falls by ``drop`` in total from day i+1's open to day i+31's, then stays."""
    steps = np.linspace(0, np.log(1 - drop), 31)
    g.open[i + 1 : i + 32, j] *= np.exp(steps)
    g.open[i + 32 :, j] *= 1 - drop


def test_abnormal_is_the_coin_less_the_other_coins() -> None:
    g = coins(3, 50)
    g.open[:] = 100.0
    g.open[31, 0] = 90.0  # coin 0: -10% over the window
    g.open[31, 1] = 110.0  # coin 1: +10%; coin 2 flat
    assert ev.abnormal(g, 0, 0, 1, 31) == pytest.approx(-0.10 - 0.05)
    g.open[31, 2] = np.nan  # a coin missing at one end is left out of the others
    assert ev.abnormal(g, 0, 0, 1, 31) == pytest.approx(-0.20)
    assert ev.abnormal(g, 2, 0, 1, 31) is None
    assert ev.window_return(g, 0, 45, 1, 31) is None  # past the data


def test_nothing_is_measured_across_a_break_in_a_coins_history() -> None:
    g = coins(3, 60)
    g.open[:] = 100.0
    g.open[20:, 1] = 10_000.0  # coin 1 is swapped 100:1 after a one-day gap
    g.open[19, 1] = np.nan
    g.age[20:, 1] = np.arange(1, 41)  # a new history starts
    assert ev.window_return(g, 1, 0, 1, 31) is None
    assert ev.abnormal(g, 0, 0, 1, 31) == pytest.approx(0.0)  # the swap isn't in the others
    assert ev.window_return(g, 1, 20, 1, 31) == pytest.approx(0.0)  # inside one history: fine


def test_a_planted_fall_is_found_and_the_placebo_sets_it_apart() -> None:
    g = coins(30, 1400, noise=0.002)  # quiet coins, so the planted fall is measured precisely
    events = []
    for k in range(60):  # 60 listing days, 30 coins, each a 10% fall after its day 0
        j, i = k % 30, 200 + 15 * k
        plant(g, j, i, 0.10)
        events.append(ev.Event(g.symbols[j], g.days[i]))
    r = ev.study(g, events, verdict=ev.perp_verdict, placebo_days=50)
    assert r.event_days == 60
    assert r.mean == pytest.approx(-0.10, abs=0.02)
    assert r.p_below > 0.99
    assert r.placebo_share_at_or_below < 0.05
    assert r.verdict == "EXIT RULE"


def test_no_effect_is_a_kill_and_too_few_events_insufficient() -> None:
    g = coins(30, 1400)
    events = [ev.Event(g.symbols[k % 30], g.days[200 + 15 * k]) for k in range(60)]
    r = ev.study(g, events, verdict=ev.perp_verdict, placebo_days=50)
    assert abs(r.mean) < 0.03
    assert r.verdict == "KILL"
    few = ev.study(g, events[:10], verdict=ev.perp_verdict, placebo_days=10)
    assert few.verdict == "INSUFFICIENT"


def test_same_day_events_count_once() -> None:
    g = coins(10, 400)
    same = [ev.Event(g.symbols[j], g.days[100]) for j in range(3)]
    r = ev.study(g, same, placebo_days=5)
    assert (r.events, r.event_days) == (3, 1)


def test_perp_listings_need_spot_history_and_a_window_before_the_holdout() -> None:
    g = coins(5, 400)
    g.age[:, 1] = np.maximum(0, g.age[:, 1] - 250)  # coin 1 listed on spot on day 250
    at = lambda i: datetime.combine(g.days[i], datetime.min.time(), UTC)  # noqa: E731
    first = {
        "C00USDT": at(100),  # counts
        "C01USDT": at(280),  # only 30 days of spot before: the perp came too soon
        "C02USDT": datetime(2019, 12, 31, tzinfo=UTC),  # before the archive: censored
        "C03USDT": at(390),  # its 30 days run into the holdout
        "C04USDT": None,  # no perp
    }
    holdout = at(399)
    found = ev.perp_listings(g, first, holdout)
    assert found == [ev.Event("C00USDT", g.days[100])]
