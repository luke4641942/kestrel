"""Safety for paper trading: halts that only a person lifts, the [risk] limits, stale data,
reconciliation and the kill switch."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from kestrel.backtest.costs import Fill, Rejection, Side
from kestrel.config import Config, RiskConfig
from kestrel.data.schema import Interval
from kestrel.execution import safety
from kestrel.execution.paper import PaperAccount, Terms, run
from kestrel.execution.venue import SimVenue
from tests.backtest.helpers import T0, bars, config

D = Decimal
D1 = Interval.D1
DAY = timedelta(days=1)
MA = {"fast": 5, "slow": 20}


def limits(order: str = "1e12", daily: str = "0.99", drawdown: str = "0.99") -> Config:
    return replace(config(), risk=RiskConfig(D(order), D(daily), D(drawdown)))


def rising(n: int = 60):  # type: ignore[no-untyped-def]
    closes = [100.0 * 1.01**i for i in range(n)]
    return bars([closes[0], *closes[:-1]], closes=closes, step=DAY)


def account(tmp_path: Path, cash: str = "1000", start: datetime = T0) -> PaperAccount:
    return PaperAccount.open(tmp_path, Terms("acct", "ma-trend", "BTCUSDT", D1, MA, D(cash), start))


def of(acct: PaperAccount, kind: str) -> list[dict]:  # type: ignore[type-arg]
    return [e for e in acct.ledger.events() if e["type"] == kind]


def test_a_halt_holds_until_a_person_resumes_it_with_a_reason(tmp_path: Path) -> None:
    acct = account(tmp_path)
    assert safety.halted(acct) is None
    with pytest.raises(ValueError, match="isn't halted"):
        safety.resume(acct, "fine")
    safety.halt(acct, "testing")
    assert safety.halted(acct) == "testing"
    with pytest.raises(ValueError, match="halted \\(testing\\)"):
        run(acct, rising(), config())
    with pytest.raises(ValueError, match="record why"):
        safety.resume(acct, "  ")
    safety.resume(acct, "checked the ledger")
    assert safety.halted(acct) is None
    assert run(acct, rising(), config())  # trades again
    assert of(acct, "resume")[0]["reason"] == "checked the ledger"


def test_loss_limits_at_each_mark(tmp_path: Path) -> None:
    acct = account(tmp_path)
    risk = RiskConfig(D(10**6), D("0.15"), D("0.5"))
    assert safety.check_mark(acct, D(1), risk) is None  # no marks yet: nothing to compare
    for equity in ("1000", "1200", "1100"):
        acct.ledger.append({"type": "mark", "equity": equity})
    assert safety.check_mark(acct, D(1000), risk) is None  # -9% on the day, -17% from peak
    assert "fell 18.2% since the last mark" in safety.check_mark(acct, D(900), risk)  # type: ignore[operator]
    acct.ledger.append({"type": "mark", "equity": "650"})
    assert "drawdown 50.8% from the peak of 1200.00" in safety.check_mark(acct, D(590), risk)  # type: ignore[operator]


def test_a_breached_mark_halts_before_the_day_is_decided(tmp_path: Path) -> None:
    # 30 days rising 1% a day (the strategy goes fully in), then a 30% fall in one day.
    closes = [100.0 * 1.01**i for i in range(30)]
    closes += [closes[-1] * 0.7, closes[-1] * 0.7]
    frame = bars([closes[0], *closes[:-1]], closes=closes, step=DAY)
    acct = account(tmp_path)
    run(acct, frame, limits(daily="0.15"))
    reason = safety.halted(acct)
    assert reason is not None
    assert "since the last mark" in reason
    crash_day = frame["open_time"][30].isoformat()
    assert of(acct, "mark")[-1]["bar"] == crash_day
    assert crash_day not in [e["bar"] for e in of(acct, "decision")]  # halted before deciding


def test_an_order_over_the_limit_is_refused_and_halts(tmp_path: Path) -> None:
    acct = account(tmp_path, cash="1000")
    handled = run(acct, rising(), limits(order="500"))
    assert isinstance(handled[-1].order, Rejection)
    assert "risk limit" in handled[-1].order.reason
    assert "over the 500 limit" in (safety.halted(acct) or "")
    assert of(acct, "fill") == []


def test_the_guard_passes_small_orders_through() -> None:
    venue = SimVenue(config(), D(1000))
    guard = safety.RiskGuard(venue, RiskConfig(D(500), D(1), D(1)))
    guard.set_market("BTCUSDT", D(100), D(10**9))
    assert isinstance(guard.submit_market("BTCUSDT", Side.BUY, D(4)), Fill)
    assert guard.tripped is None
    assert guard.position("BTCUSDT") == D(4)
    assert guard.cash() < D(600)
    assert guard.cost_model() is venue.cost_model()


def test_stale_and_gappy_bars(tmp_path: Path) -> None:
    frame = rising(40)
    last = frame["open_time"][-1]
    fresh = last + DAY + timedelta(hours=1)  # the last bar closed an hour ago
    assert safety.stale(frame, D1, fresh) is None
    late = safety.stale(frame, D1, fresh + DAY)
    assert late is not None
    assert "has closed" in late
    gappy = frame.filter(frame["open_time"] != frame["open_time"][-5])
    assert "1 bars missing in the last 30" in (safety.stale(gappy, D1, fresh) or "")
    assert safety.stale(frame.clear(), D1, fresh) == "no bars stored"
    acct = account(tmp_path, start=last)
    with pytest.raises(ValueError, match="stale data"):
        run(acct, frame, config(), now=fresh + DAY)
    assert safety.halted(acct) is None  # stale data stops the run; it doesn't halt
    assert run(acct, frame, config(), lambda _: D(150), now=fresh)


def test_reconciliation_lists_what_the_exchange_lacks() -> None:
    held = [("BTC", D("0.5")), ("USDT", D(200))]
    assert safety.reconcile(held, {"BTC": D(1), "USDT": D(10_000)}) == []
    short = safety.reconcile(held, {"USDT": D(100)})
    assert [(h.asset, h.exchange) for h in short] == [("BTC", D(0)), ("USDT", D(100))]
    assert (
        safety.describe(short)
        == "BTC: ledger 0.5, exchange holds 0; USDT: ledger 200, exchange holds 100"
    )


def test_kill_sells_everything_and_halts(tmp_path: Path) -> None:
    acct = account(tmp_path)
    run(acct, rising(), config())
    assert acct.state().position > 0
    venue = SimVenue(config(), D(1000))
    event = safety.kill(acct, venue, D(200), D(10**9), "testing the switch")
    assert event["side"] == "sell"
    assert acct.state().position == 0
    assert acct.state().cash > 0
    assert safety.halted(acct) == "killed: testing the switch"
    again = safety.kill(acct, SimVenue(config(), D(1000)), D(200), D(10**9), "twice")
    assert "side" not in again  # nothing left to sell


def test_a_kill_the_market_refuses_is_recorded_and_still_halts(tmp_path: Path) -> None:
    acct = account(tmp_path)
    run(acct, rising(), config())
    event = safety.kill(acct, SimVenue(config(), D(1000)), D(200), D(1), "thin market")
    assert "of the last bar's volume" in event["rejected"]
    assert acct.state().position > 0  # still held: a person must sell it
    assert safety.halted(acct) == "killed: thin market"


def test_a_live_venue_keeps_its_flag_through_the_guard() -> None:
    class Live(SimVenue):
        live = True

    assert safety.RiskGuard(Live(config(), D(1)), config().risk).live
    assert not safety.RiskGuard(SimVenue(config(), D(1)), config().risk).live
    assert UTC  # the module's clock is UTC throughout
