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


def chosen_sunday(panel: pl.DataFrame) -> Any:
    """A Sunday the strategy holds coins on, late enough for a year of history."""
    from kestrel.strategy import make_portfolio_strategy as make

    picks = make(KEY, {}).target_weights(panel).filter(pl.col("weight") > 0)
    return picks["open_time"].max()


def test_a_testnet_basket_trades_only_the_latest_bar_one_order_per_coin(tmp_path: Path) -> None:
    from kestrel.backtest.costs import CostModel, Fill
    from kestrel.execution.binance import BinanceVenue
    from tests.test_binance_venue import FakeBinance

    panel = coins()
    sunday = chosen_sunday(panel)
    upto = panel.filter(pl.col("open_time") <= sunday)
    fake = FakeBinance()
    fake.commission = ("0.01", "USDT")
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    venue = BinanceVenue(fake.client(), symbols, "USDT", CostModel(config().costs), CASH)
    terms = Terms("t", KEY, "UNIVERSE", D1, {}, CASH, sunday - timedelta(days=3), venue="testnet")
    paper = PaperAccount.open(tmp_path, terms)
    opens = {s: Decimal(100) for s in symbols}
    handled = run_basket(paper, upto, config(), lambda _: opens, venue=venue)
    assert [h.missed for h in handled] == [True, True, True, False]  # a live venue can't
    assert handled[-1].bar == sunday  # fill at a past open: only the latest trades
    bought = [o for o in handled[-1].orders if isinstance(o, Fill)]
    assert bought
    assert len(fake.orders) == len(bought)
    assert {o["symbol"] for o in fake.orders} == {o.symbol for o in bought}
    state = paper.state()
    assert set(state.positions) == {o.symbol for o in bought}
    assert state.cash < CASH


def test_a_basket_waits_for_every_coin_that_traded_yesterday() -> None:
    from kestrel.execution.paper import complete_opens

    day = timedelta(days=1)
    at = MONDAY + 10 * day
    seen = {"A": at - day, "B": at - day, "GONE": at - 30 * day}
    one = Decimal(1)
    assert complete_opens({"A": one, "B": one, "GONE": None}, seen, at, day) == {"A": one, "B": one}
    assert complete_opens({"A": one, "B": None, "GONE": None}, seen, at, day) == {}  # wait
    assert complete_opens({"A": None, "B": None}, seen, at, day) == {}  # not opened yet


def test_killing_a_basket_sells_every_coin_and_halts(tmp_path: Path) -> None:
    from kestrel.execution import safety
    from kestrel.execution.venue import SimVenue

    panel = coins()
    paper = account(tmp_path)
    run_basket(
        paper,
        panel.filter(pl.col("open_time") <= chosen_sunday(panel) + timedelta(days=1)),
        config(),
    )
    held = paper.state().positions
    assert held
    prices = {s: Decimal(100) for s in held}
    volumes = {s: Decimal(10**9) for s in held}
    venue = SimVenue(config(), CASH, rules=config().rules_for)
    event = safety.kill_basket(paper, venue, prices, volumes, "drill")
    assert {x["symbol"] for x in event["sales"]} == set(held)
    assert all("quantity" in x for x in event["sales"])
    state = paper.state()
    assert state.positions == {}
    assert state.cash > 0
    assert set(state.book) == {"last_bar", "stranded", "previous"}
    assert safety.halted(paper) == "killed: drill"


def test_tracking_compares_the_newest_day_from_the_opens_it_recorded(tmp_path: Path) -> None:
    """The newest decision trades at the forming bar's opens, which aren't stored yet; the
    decision records them, so tracking replays and compares that day too (it once stopped
    a day short, 2026-11-23)."""
    from kestrel.execution.tracking import track_basket

    panel = coins()
    last = panel["open_time"].max()
    paper = account(tmp_path)
    run_basket(paper, panel, config())
    opens = {s: Decimal("100") for s in panel["symbol"].unique().to_list()}
    run_basket(paper, panel, config(), lambda _: opens)
    newest = of(paper.ledger.events(), "decision")[-1]
    assert newest["bar"] == last.isoformat()
    assert newest["opens"] == {s: "100" for s in opens}
    older = of(paper.ledger.events(), "decision")[-2]
    assert "opens" not in older  # its next bar was stored: nothing to record

    t = track_basket(paper, panel, config())
    assert t.days[-1].bar == last  # the newest day is compared, not dropped
    assert all(d.equity == d.expected and d.target == d.expected_target for d in t.days)
