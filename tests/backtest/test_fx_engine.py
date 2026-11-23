"""The forex engine (kestrel.backtest.fx_engine): hand-checked cases for each rule, and
parity with the crypto engine where the two models meet (long only, unlevered, the same
spread, no financing)."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.engine import run_backtest
from kestrel.backtest.fx_engine import (
    FxEngineError,
    account_per_unit,
    run_fx,
    zero_rates,
)
from kestrel.config import FxConfig, parse_config
from kestrel.data import fx
from kestrel.data.schema import Interval
from tests.backtest.helpers import FixedWeights, bars

DAY = timedelta(days=1)
T0 = datetime(2010, 1, 3, 22, tzinfo=UTC)  # a Sunday, 17:00 New York
HOLDOUT = datetime(2023, 1, 1, tzinfo=UTC)


def fxc(markup: float = 0.0, cap: float = 3) -> FxConfig:
    return FxConfig(HOLDOUT, ("EURAUD",), "AUD", Decimal(str(cap)), Decimal(str(markup)))


def pair_frame(
    opens: list[float],
    closes: list[float] | None = None,
    half: float = 0.0,
    times: list[datetime] | None = None,
) -> pl.DataFrame:
    closes = closes or opens
    times = times or [T0 + i * DAY for i in range(len(opens))]
    rows = {
        "open_time": times,
        "open": opens,
        "high": [max(o, c) for o, c in zip(opens, closes, strict=True)],
        "low": [min(o, c) for o, c in zip(opens, closes, strict=True)],
        "close": closes,
    }
    for side, sign in (("bid", -1), ("ask", 1)):
        for p in ("open", "high", "low", "close"):
            rows[f"{side}_{p}"] = [v + sign * half for v in rows[p]]  # type: ignore[operator]
    rows["ticks"] = [100] * len(opens)
    return pl.DataFrame(rows, schema=fx.FX_SCHEMA)


def weights(frame: pl.DataFrame, **cols: list[float]) -> pl.DataFrame:
    return pl.DataFrame({"open_time": frame["open_time"], **cols})


def test_long_follows_the_price_and_pays_the_spread() -> None:
    f = pair_frame([100, 100, 110, 121], half=0.5)
    # A wide band: bought once, then left to drift (a band of 0 would rebalance daily).
    r = run_fx("t", {"EURAUD": f}, weights(f, EURAUD=[1.0] * 4), fxc(), 0.5, zero_rates, 1000)
    # Decided at day 0's close, bought at day 1's open: 10 units at the ask (100.5).
    (fill,) = r.fills
    assert (fill.units, fill.price, fill.spread_cost) == (10, 100.5, pytest.approx(5))
    assert r.equity["equity"].to_list() == pytest.approx([1000, 995, 1095, 1205])
    assert r.equity["gross"].to_list()[-1] == pytest.approx(1210 / 1205)


def test_short_gains_when_the_price_falls() -> None:
    f = pair_frame([100, 100, 90])
    r = run_fx("t", {"EURAUD": f}, weights(f, EURAUD=[-1.0] * 3), fxc(), 0.5, zero_rates, 1000)
    assert r.fills[0].units == -10
    assert r.equity["equity"].to_list() == pytest.approx([1000, 1000, 1100])
    assert r.equity["net"].to_list()[-1] == pytest.approx(-900 / 1100)


def test_leverage_up_to_the_cap_and_a_wipe_out_stops_the_run() -> None:
    f = pair_frame([100, 100, 65, 60])
    r = run_fx("t", {"EURAUD": f}, weights(f, EURAUD=[3.0] * 4), fxc(), 0, zero_rates, 1000)
    assert r.fills[0].units == 30  # 3x: 3000 AUD of a 1000 AUD account
    # Day 2 opens 35% lower: 30 x -35 = -1050, more than the account. Lost, and the run
    # stops there rather than trading a negative balance.
    assert r.wiped_out == T0 + 2 * DAY
    assert r.final_equity == 0
    assert r.equity.height == 3
    with pytest.raises(FxEngineError, match=r"3.50x, over the 3x cap"):
        run_fx("t", {"EURAUD": f}, weights(f, EURAUD=[3.5] * 4), fxc(), 0, zero_rates, 1000)


def test_financing_by_calendar_night_with_the_markup_on_both_sides() -> None:
    thursday = T0 + 4 * DAY
    times = [T0, T0 + DAY, thursday, thursday + 3 * DAY, thursday + 4 * DAY]  # Sun Mon Thu Sun Mon

    def rates(currency: str, at: datetime) -> float:
        return {"EUR": 0.05, "AUD": 0.01}[currency]

    f = pair_frame([100.0] * 5, times=times)
    long = run_fx("t", {"EURAUD": f}, weights(f, EURAUD=[1.0] * 5), fxc(markup=1), 0.5, rates, 1000)
    # Held from Monday's open: nights after Mon (to Thu: 3), Thu (to Sun: 3), Sun (1), Mon (1).
    daily = 1000 * (0.05 - 0.01 - 0.01) / 365
    assert long.financing == pytest.approx(daily * 8)
    short = run_fx(
        "t", {"EURAUD": f}, weights(f, EURAUD=[-1.0] * 5), fxc(markup=1), 0.5, rates, 1000
    )
    assert short.financing == pytest.approx(1000 * (0.01 - 0.05 - 0.01) / 365 * 8)


def test_conversion_to_the_account_through_usd() -> None:
    mids = {"AUDUSD": 0.5, "USDJPY": 100.0, "EURUSD": 1.2}
    assert account_per_unit("USD", "AUD", mids) == pytest.approx(2.0)
    assert account_per_unit("JPY", "AUD", mids) == pytest.approx(0.02)
    assert account_per_unit("EUR", "AUD", mids) == pytest.approx(2.4)
    assert account_per_unit("AUD", "AUD", {}) == 1.0
    assert account_per_unit("CHF", "AUD", mids) is None


def test_a_yen_pair_is_sized_and_marked_in_aud() -> None:
    f = pair_frame([100.0, 100.0, 110.0])  # USDJPY
    aud = pair_frame([0.5, 0.5, 0.5])  # AUDUSD: 1 AUD = 0.5 USD
    cfg = FxConfig(HOLDOUT, ("USDJPY", "AUDUSD"), "AUD", Decimal(3), Decimal(0))
    r = run_fx(
        "t",
        {"USDJPY": f},
        weights(f, USDJPY=[1.0] * 3),
        cfg,
        0,
        zero_rates,
        1000,
        conversion={"AUDUSD": aud},
    )
    # 1000 AUD is 500 USD: buy 500 USD of USDJPY. +10 yen a dollar is 5000 JPY = 100 AUD.
    assert r.fills[0].units == pytest.approx(500)
    assert r.final_equity == pytest.approx(1100)


def test_a_holiday_is_neither_traded_nor_marked() -> None:
    a = pair_frame([100, 100, 110, 120])
    b_times = [T0, T0 + DAY, T0 + 3 * DAY]  # no candle on day 2
    b = pair_frame([100, 100, 130], times=b_times)
    w = pl.DataFrame({"open_time": a["open_time"], "EURAUD": [0.0] * 4, "GBPAUD": [1.0] * 4})
    cfg = FxConfig(HOLDOUT, ("EURAUD", "GBPAUD"), "AUD", Decimal(3), Decimal(0))
    r = run_fx("t", {"EURAUD": a, "GBPAUD": b}, w, cfg, 0, zero_rates, 1000)
    # GBPAUD bought at day 1's open (10 units); day 2 it has no candle, so equity holds.
    assert r.equity["equity"].to_list() == pytest.approx([1000, 1000, 1000, 1300])


def test_the_band_skips_small_changes_and_counts_them() -> None:
    f = pair_frame([100.0] * 5)
    w = weights(f, EURAUD=[1.0, 1.01, 1.01, 0.5, 0.5])
    r = run_fx("t", {"EURAUD": f}, w, fxc(), 0.02, zero_rates, 1000)
    assert [x.units for x in r.fills] == pytest.approx([10, -5])
    assert r.band_skips == 1


def test_refusals() -> None:
    f = pair_frame([100.0] * 3)
    with pytest.raises(FxEngineError, match="no weights for EURAUD"):
        run_fx(
            "t",
            {"EURAUD": f},
            pl.DataFrame({"open_time": f["open_time"]}),
            fxc(),
            0,
            zero_rates,
            1000,
        )
    late = pair_frame([100.0] * 3, times=[HOLDOUT + i * DAY for i in range(3)])
    with pytest.raises(FxEngineError, match="forex holdout"):
        run_fx("t", {"EURAUD": late}, weights(late, EURAUD=[0.0] * 3), fxc(), 0, zero_rates, 1000)
    allowed = run_fx(
        "t",
        {"EURAUD": late},
        weights(late, EURAUD=[0.0] * 3),
        fxc(),
        0,
        zero_rates,
        1000,
        allow_holdout=True,
    )
    assert allowed.used_holdout
    with pytest.raises(FxEngineError, match="isn't a number"):
        run_fx("t", {"EURAUD": f}, weights(f, EURAUD=[math.nan] * 3), fxc(), 0, zero_rates, 1000)


def test_weekend_candles_are_dropped_and_counted() -> None:
    sat = datetime(2005, 1, 1, 22, tzinfo=UTC)  # Saturday 17:00 New York
    f = pair_frame([1.0, 1.0, 1.0], times=[sat, sat + DAY, sat + 2 * DAY])
    kept, dropped = fx.trading_days(f)
    assert dropped == 1
    assert kept["open_time"].to_list() == [sat + DAY, sat + 2 * DAY]


def test_parity_with_the_crypto_engine_long_only_and_unlevered() -> None:
    """Where the models meet they must agree: one market quoted in the account currency,
    weights in [0, 1], the same half-spread and no other costs, no band, no financing.
    Tolerances as the crypto engines' own parity test (the crypto engine sizes a purchase
    so it fits in cash after costs; the forex engine, with margin, doesn't need to)."""
    rnd = random.Random(11)
    n, price, half_bps = 1500, 100.0, 1
    opens, closes = [], []
    for _ in range(n):
        o = price * math.exp(rnd.gauss(0, 0.001))
        c = o * math.exp(rnd.gauss(0.0002, 0.01))
        opens.append(o)
        closes.append(c)
        price = c
    w = [0.3 if (i // 40) % 2 else 0.9 for i in range(n)]
    crypto_bars = bars(opens, closes=closes, step=DAY, start=T0, quote_volume=1e12)
    cfg = parse_config(
        {
            "account_currency": "USDT",
            "costs": {
                "taker_fee_bps": 0,
                "half_spread_bps": half_bps,
                "impact_coef_bps": 0,
                "max_participation": 0.01,
            },
            "backtest": {
                "no_trade_band": 0,
                "risk_per_trade": 0.01,
                "holdout_start": HOLDOUT.date(),
            },
            "instruments": {
                "BTCUSDT": {"step_size": "0.00000001", "min_qty": "0", "min_notional": "0"}
            },
            "universe": {
                "step_size": "0.00000001",
                "min_qty": "0",
                "min_notional": "0",
                "volume_window_days": 30,
                "spread_tiers": [[0, half_bps]],
            },
            "risk": {"max_order_usdt": "1e12", "max_daily_loss": 0.99, "max_drawdown": 0.99},
        }
    )
    crypto = run_backtest(FixedWeights(w), crypto_bars, Interval.D1, cfg, Decimal(1_000_000))

    rows = {
        "open_time": crypto_bars["open_time"],
        "open": opens,
        "close": closes,
        "high": crypto_bars["high"],
        "low": crypto_bars["low"],
    }
    h = half_bps / 10_000
    frame = pl.DataFrame(
        {
            **rows,
            **{
                f"bid_{p}": [v * (1 - h) for v in rows[p]] for p in ("open", "high", "low", "close")
            },
            **{
                f"ask_{p}": [v * (1 + h) for v in rows[p]] for p in ("open", "high", "low", "close")
            },
            "ticks": [100] * n,
        }
    ).select(list(fx.FX_SCHEMA))
    forex = run_fx(
        "t",
        {"BTCAUD": frame},
        weights(frame, BTCAUD=w),
        FxConfig(HOLDOUT, ("BTCAUD",), "AUD", Decimal(1), Decimal(0)),
        0,
        zero_rates,
        1_000_000,
    )
    e_c = crypto.equity["equity"]
    e_f = forex.equity["equity"]
    assert e_c.len() == e_f.len()
    r_c, r_f = e_c.pct_change().drop_nulls(), e_f.pct_change().drop_nulls()
    assert (r_c - r_f).abs().max() < 1e-4
    assert abs(e_c[-1] / e_f[-1] - 1) < 2e-3
    crypto_spread = sum(float(x.fill.spread_cost) for x in crypto.fills)
    assert forex.spread_cost == pytest.approx(crypto_spread, rel=0.01)
