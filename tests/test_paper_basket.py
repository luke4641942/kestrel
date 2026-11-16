"""Paper trading a basket strategy: a day-by-day replay must reproduce the portfolio
backtest exactly, resume from its ledger, and trade live opens or wait for them."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from kestrel.backtest.portfolio import run_portfolio
from kestrel.data.schema import Interval
from kestrel.execution.paper import PaperAccount, Terms, run_basket
from kestrel.strategy import make_portfolio_strategy
from tests.backtest.helpers import config
from tests.test_btc_rotation import MONDAY, coins

D1 = Interval.D1
KEY = "btc-rotation-4"
CASH = Decimal(1000)


def account(tmp_path: Path) -> PaperAccount:
    return PaperAccount.open(tmp_path, Terms("basket", KEY, "UNIVERSE", D1, {}, CASH, MONDAY))


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


def fills(events: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    return [
        (e["at"], e["symbol"], e["side"], e["quantity"], e["price"], e["fee"],
         e["spread_cost"], e["impact_cost"])
        for e in of(events, "fill")
    ]  # fmt: skip


def test_a_day_by_day_replay_reproduces_the_portfolio_backtest(tmp_path: Path) -> None:
    panel = coins()
    result = run_portfolio(make_portfolio_strategy(KEY, {}), panel, D1, config(), CASH)
    paper = account(tmp_path)
    handled = run_basket(paper, panel, config())
    assert len(handled) == panel["open_time"].n_unique() - 1  # the last next open is unknown
    events = paper.ledger.events()
    expected = [
        (r.time.isoformat(), r.fill.symbol, r.fill.side.value, str(r.fill.quantity),
         str(r.fill.price), str(r.fill.fee), str(r.fill.spread_cost), str(r.fill.impact_cost))
        for r in result.fills
    ]  # fmt: skip
    assert fills(events) == expected
    assert len(expected) >= 4
    assert {f[2] for f in expected} == {"buy", "sell"}  # it entered and left positions
    marks = [float(Decimal(e["equity"])) for e in of(events, "mark")]
    assert marks == pytest.approx(result.equity["equity"].to_list()[:-1], rel=1e-12)
    decided = [e for e in of(events, "decision") if e["targets"] is not None]
    assert decided  # Sunday decisions are on record


def test_it_resumes_from_its_ledger_as_if_it_never_stopped(tmp_path: Path) -> None:
    panel = coins()
    whole = account(tmp_path / "a")
    run_basket(whole, panel, config())
    split = account(tmp_path / "b")
    cut = MONDAY + timedelta(days=400)
    run_basket(split, panel.filter(pl.col("open_time") <= cut), config())
    state = split.state()
    assert state.last_bar is not None
    run_basket(split, panel, config())
    assert fills(split.ledger.events()) == fills(whole.ledger.events())
    assert whole.state() == split.state()


def test_a_live_run_trades_the_forming_opens_or_waits_for_them(tmp_path: Path) -> None:
    panel = coins()
    last = panel["open_time"].max()
    paper = account(tmp_path)
    handled = run_basket(paper, panel, config())  # replay up to the last stored bar
    assert handled[-1].bar == last - timedelta(days=1)
    assert run_basket(paper, panel, config(), lambda _: {}) == []  # tomorrow hasn't opened
    opens = {s: Decimal("100") for s in panel["symbol"].unique().to_list()}
    asked: list[object] = []

    def forming(at):  # type: ignore[no-untyped-def]
        asked.append(at)
        return opens

    latest = run_basket(paper, panel, config(), forming)
    assert [h.bar for h in latest] == [last]
    assert asked == [last + timedelta(days=1)]


def test_a_basket_state_carries_positions_and_the_step_book(tmp_path: Path) -> None:
    paper = account(tmp_path)
    run_basket(paper, coins(), config())
    state = paper.state()
    assert set(state.positions) <= {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"}
    assert set(state.book) == {"last_bar", "stranded", "previous"}
    assert state.position == 0  # single-coin field unused for a basket
