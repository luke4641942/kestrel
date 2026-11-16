"""The hypothetical shorting studies: the long side must be the pursued rule exactly, and the
short side's returns, costs, funding and close-outs are checked by hand."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from kestrel.strategies.btc_regime import BtcRegimeTrend
from kestrel.strategies.btc_rotation import BtcRegimeRotation4
from kestrel.strategies.trend import TrendVolTarget
from kestrel.studies import shorting as sh
from tests.test_btc_rotation import MONDAY, coins

TIERS = [(500e6, 1e-4), (100e6, 3e-4), (20e6, 8e-4), (0.0, 15e-4)]
FEE = 7.5e-4


def full_grid() -> sh.Grid:
    panel = coins()
    return sh.grid(panel, {s: None for s in panel["symbol"].unique().to_list()}, TIERS)


def test_the_long_sides_are_the_pursued_rules_exactly() -> None:
    panel = coins()
    g = full_grid()
    b, e = g.symbols.index("BTCUSDT"), g.symbols.index("ETHUSDT")
    eth = panel.filter(pl.col("symbol") == "ETHUSDT").sort("open_time")
    btc = panel.filter(pl.col("symbol") == "BTCUSDT").sort("open_time")
    bars = eth.with_columns(btc_close=btc["close"])
    lo, ls = sh.regime_trend_weights(g.close[:, e], g.close[:, b])
    assert lo == pytest.approx(BtcRegimeTrend("ETHUSDT").target_weights(bars).to_numpy())
    assert (ls < 0).any()  # the mirror shorts sometimes
    assert np.allclose(ls[lo > 0], lo[lo > 0])  # it never changes a long day
    assert (lo[ls < 0] == 0).all()  # and only shorts on days the long rule is in cash
    tvt_lo, tvt_ls = sh.trend_votes_weights(g.close[:, e])
    assert tvt_lo == pytest.approx(TrendVolTarget("ETHUSDT").target_weights(eth).to_numpy())
    assert tvt_ls.max() <= 1
    wave = 100 * np.exp(0.3 * np.sin(np.arange(400) / 40))  # rises and falls
    up_only, both = sh.trend_votes_weights(wave)
    assert both.min() < 0 < both.max() <= 1
    assert (up_only >= both).all()  # (up - down) / 3 never exceeds up / 3


def test_the_rotation_long_side_is_the_strategy_and_the_short_side_takes_the_weakest() -> None:
    panel = coins(btc_drift=0.0)  # BTC wanders, so the regime turns both ways
    g = sh.grid(panel, {}, TIERS)
    b = g.symbols.index("BTCUSDT")
    strategy = BtcRegimeRotation4()
    lo = sh.rotation_weights(g, b, top_n=1, short_side=False)
    decided = strategy.target_weights(panel).filter(pl.col("symbol").is_not_null())
    for t, sym, w in decided.select("open_time", "symbol", "weight").iter_rows():
        i = g.days.index(t.date())
        assert lo[i, g.symbols.index(sym)] == pytest.approx(w)
    held = {(t.date(), s) for t, s in decided.select("open_time", "symbol").iter_rows()}
    for i, d in enumerate(g.days):
        if d.isoweekday() == 7:
            for j, s in enumerate(g.symbols):
                assert (lo[i, j] > 0) == ((d, s) in held)
    ls = sh.rotation_weights(g, b, top_n=1, short_side=True)
    assert np.allclose(np.maximum(ls, 0), lo)  # the long side is untouched
    shorts = [(i, j) for i, j in zip(*np.nonzero(ls < 0), strict=True)]
    assert shorts
    i, j = shorts[0]
    sunday = max(k for k in range(i + 1) if g.days[k].isoweekday() == 7)
    r = sh.momentum(g, sunday, 90)
    ok = [k for k in range(len(g.symbols)) if np.isfinite(r[k]) and g.age[sunday, k] >= 365]
    assert j == min(ok, key=lambda k: r[k])  # the weakest coin


def one_coin(opens: list[float], highs: list[float] | None = None) -> sh.Grid:
    n = len(opens)
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
    o = np.array(opens, dtype=float)[:, None]
    return sh.Grid(
        days=days,
        symbols=["XUSDT"],
        open=o,
        high=np.array(highs or opens, dtype=float)[:, None],
        close=np.vstack([o[1:], o[-1:]]),
        band=np.full((n, 1), 1e-4),
        funding=np.full((n, 1), 3e-4),
        volume=np.full((n, 1), 1e9),
        age=np.arange(1, n + 1, dtype=float)[:, None],
        perp=np.ones((n, 1), dtype=bool),
    )


def test_a_short_earns_the_fall_and_pays_its_costs() -> None:
    opens = [100 * 0.99**i for i in range(10)]
    g = one_coin(opens)
    w = np.full((10, 1), -1.0)
    cfd = sh.simulate(g, w, FEE, "cfd")
    assert len(cfd.total) == 9  # the last day has no next open
    assert cfd.total[0] == 0  # decided at day 0's close, held from day 1
    entry = 0.01 - 2 * 1e-4 - 0.05 / 365  # the fall, a CFD spread both sides, a day's financing
    assert cfd.total[1] == pytest.approx(entry)
    assert cfd.total[2] == pytest.approx(0.01 - 0.05 / 365)
    assert cfd.short_trades == 1
    assert cfd.long_leg.sum() == 0
    perp = sh.simulate(g, w, FEE, "perp")
    assert perp.total[2] == pytest.approx(0.01 + 3e-4)  # positive funding pays the short
    assert perp.total[1] == pytest.approx(0.01 + 3e-4 - (5e-4 + 1e-4))
    assert sh.simulate(g, w, FEE, "cfd-stress").total[2] == pytest.approx(0.01 - 0.15 / 365)


def test_a_squeeze_closes_the_short_out_and_a_long_pays_spot_costs() -> None:
    g = one_coin([100.0] * 6, highs=[100, 100, 100, 160, 100, 100])
    out = sh.simulate(g, np.full((6, 1), -0.5), FEE, "cfd")
    assert out.close_outs == 1
    assert out.total[3] == pytest.approx(-0.25 - 0.5 * 0.05 / 365)  # half of the half
    up = sh.simulate(g, np.full((6, 1), 1.0), FEE, "cfd")
    assert up.total[1] == pytest.approx(-(FEE + 1e-4))
    assert up.long_trades == 1


def test_a_coin_that_stops_trading_is_closed_and_held_no_longer() -> None:
    g = one_coin([100, 100, 100, 90, float("nan"), float("nan")])
    g.close[3, 0] = 80.0  # its last close
    out = sh.simulate(g, np.full((6, 1), 1.0), 0.0, "cfd")
    assert out.long_gross[3] == pytest.approx(80 / 90 - 1)
    assert out.long_gross[4] == 0


def test_long_short_momentum_needs_a_perp_and_enough_coins() -> None:
    g = full_grid()
    g.volume[:] = 1e9
    assert not sh.long_short_momentum_weights(g).any()  # 4 coins: fewer than 6
    for name in ("A", "B"):  # two more coins, copies with a twist
        g = extend(g, name)
    g.perp[:] = True
    w = sh.long_short_momentum_weights(g)
    i = max(k for k, d in enumerate(g.days) if d.isoweekday() == 7)
    r = sh.momentum(g, i, 90)
    order = np.argsort(-r)
    assert (w[i, order[:3]] > 0).all()
    assert (w[i, order[3:]] < 0).all()
    assert w[i].sum() == pytest.approx(w[i][w[i] > 0].sum() + w[i][w[i] < 0].sum())
    g.perp[:, order[5]] = False  # the weakest had no perp: too few shortable coins
    assert not sh.long_short_momentum_weights(g)[i].any()


def extend(g: sh.Grid, name: str) -> sh.Grid:
    k = len(g.symbols)
    twist = np.linspace(0.8, 1.3, len(g.days))[:, None] * (1 + 0.1 * k)

    def add(a: np.ndarray, col: np.ndarray) -> np.ndarray:
        return np.hstack([a, col])

    return sh.Grid(
        g.days, [*g.symbols, f"{name}USDT"],
        add(g.open, g.open[:, :1] * twist), add(g.high, g.high[:, :1] * twist),
        add(g.close, g.close[:, :1] * twist), add(g.band, g.band[:, :1]),
        add(g.funding, g.funding[:, :1]), add(g.volume, g.volume[:, :1]),
        add(g.age, g.age[:, :1]), add(g.perp, g.perp[:, :1]),
    )  # fmt: skip


def test_carry_earns_funding_on_the_hedged_part_after_entry_costs() -> None:
    n = 60
    g = one_coin([100.0] * n)
    start = datetime(2020, 1, 1, tzinfo=UTC)
    times = [start + timedelta(hours=8 * k) for k in range(3 * n)]
    rates = {"XUSDT": pl.DataFrame({"time": times, "rate": [1e-4] * len(times)})}
    weeks, r, trades, share, earned, calls = sh.carry(g, rates, FEE, start=start)
    hedge = 2 / 3  # one coin: all its equity, 2/3 of it hedged
    assert trades == 1
    assert calls == 0
    assert earned[1] == pytest.approx(hedge * 21 * 1e-4)
    trip = (FEE + 1e-4) + (5e-4 + 1e-4)
    assert r[0] == 0  # only 15 settlements before the first Sunday: not yet known
    assert r[1] == pytest.approx(hedge * 21 * 1e-4 - hedge * trip)  # entered, then earns
    assert r[2] == pytest.approx(hedge * 21 * 1e-4)
    assert share["XUSDT"] == pytest.approx((len(r) - 1) / len(r))
    negative = {"XUSDT": rates["XUSDT"].with_columns(rate=pl.lit(-1e-4))}
    assert sh.carry(g, negative, FEE, start=start)[2] == 0  # never entered
    assert weeks[0].isoweekday() == 1


def test_measures() -> None:
    rising = np.full(100, 0.01)
    assert sh.p_positive(rising) == 1.0
    assert sh.p_positive(-rising) == 0.0
    assert sh.max_drawdown(np.array([0.1, -0.5, 0.2])) == pytest.approx(-0.5)
    days = [date(2021, 1, 4) + timedelta(days=i) for i in range(14)]
    assert len(sh.weekly(days, np.full(14, 0.01))) == 2
    assert sh.beta(rising * np.arange(100), 2 * rising * np.arange(100)) == pytest.approx(0.5)
    assert MONDAY.isoweekday() == 1


def test_a_saved_study_is_served_to_the_dashboard(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from kestrel.api.app import create_app
    from tests.test_api_paper import REPO_CONFIG

    ends = [date(2020, 1, 13) + timedelta(days=7 * k) for k in range(3)]
    passed = sh.CarryResult(
        weeks=3, first=ends[0], last=ends[-1], mean_weekly=0.001, p=1.0, annual=0.06,
        years={2020: 0.003}, max_drawdown=0.0, trades=1, held_share={}, funding_by_year={},
        worst_week=0.001, margin_calls=0, annual_at_baseline=0.05,
        tests={"beats a 4% stablecoin yield": True},
    )  # fmt: skip
    outcome = sh.Outcome(
        "cash-and-carry", "report text", [("UNIVERSE", 1.0)], "NEEDS-SHORTING", {}, [passed],
        {"Cash-and-carry": (ends, np.array([0.01, 0.0, 0.02]))},
    )  # fmt: skip
    path = sh.save(outcome, "cash-and-carry-note", tmp_path, "abc123")
    assert path.name == "cash-and-carry.json"
    client = TestClient(create_app(tmp_path, static_dir=None, config_path=REPO_CONFIG))
    (study,) = client.get("/api/short-studies").json()
    assert (study["verdict"], study["note"], study["commit"]) == (
        "NEEDS-SHORTING", "cash-and-carry-note", "abc123",
    )  # fmt: skip
    assert study["tests"] == {"beats a 4% stablecoin yield": True}
    curve = study["curves"][0]
    assert curve["label"] == "Cash-and-carry"
    assert curve["series"]["equity"] == pytest.approx([1.0, 1.01, 1.01, 1.0302])
    assert len(curve["series"]["time"]) == 4  # a start point one week before the first end
    assert curve["series"]["drawdown"][-1] == 0
