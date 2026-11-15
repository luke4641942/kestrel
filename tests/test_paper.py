"""Paper trading on the simulated venue: a day-by-day replay must reproduce the backtest
exactly, and an account must resume from its ledger as if it had never stopped."""

from __future__ import annotations

import math
import random
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from kestrel.backtest.engine import BacktestResult, run_backtest
from kestrel.data.schema import Interval
from kestrel.execution.paper import PaperAccount, Terms, run
from kestrel.execution.venue import SimVenue
from kestrel.strategy import make_strategy
from tests.backtest.helpers import T0, bars, config

D1 = Interval.D1
MA = {"fast": 5, "slow": 20}
DAY = timedelta(days=1)


def walk(n: int, seed: int = 3):  # type: ignore[no-untyped-def]
    rnd = random.Random(seed)
    closes, price = [], 100.0
    for _ in range(n):
        price *= math.exp(rnd.gauss(0.0008, 0.03))
        closes.append(price)
    return bars([closes[0], *closes[:-1]], closes=closes, step=DAY)


def account(
    tmp_path: Path, key: str = "ma-trend", params: dict[str, int] | None = None
) -> PaperAccount:
    terms = Terms("test", key, "BTCUSDT", D1, MA if params is None else params, Decimal(1000), T0)
    return PaperAccount.open(tmp_path, terms)


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


def backtest(frame, key: str = "ma-trend", params: dict[str, int] | None = None) -> BacktestResult:  # type: ignore[no-untyped-def]
    strategy = make_strategy(key, "BTCUSDT", MA if params is None else params)
    return run_backtest(strategy, frame, D1, config(), Decimal(1000))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("key", "params"),
    [
        ("ma-trend", {"fast": 5, "slow": 20}),
        ("trend-vol-target", {}),
        ("btc-regime-trend", {}),  # reads btc_close, a feature column
    ],
)
def test_a_day_by_day_replay_reproduces_the_backtest(
    tmp_path: Path, key: str, params: dict[str, int]
) -> None:
    frame = walk(300).with_columns(btc_close=walk(300, seed=11)["close"])
    result = backtest(frame, key, params)
    paper = account(tmp_path, key, params)
    handled = run(paper, frame, config())
    assert len(handled) == frame.height - 1  # the last bar's next open isn't known
    events = paper.ledger.events()

    fills = [
        (
            e["at"],
            e["side"],
            e["quantity"],
            e["price"],
            e["fee"],
            e["spread_cost"],
            e["impact_cost"],
        )
        for e in of(events, "fill")
    ]
    expected = [
        (r.time.isoformat(), r.fill.side.value, str(r.fill.quantity), str(r.fill.price),
         str(r.fill.fee), str(r.fill.spread_cost), str(r.fill.impact_cost))
        for r in result.fills
    ]  # fmt: skip
    assert fills == expected
    assert len(fills) > 5
    assert [e["reason"] for e in of(events, "rejection")] == [
        r.rejection.reason for r in result.rejections
    ]
    assert sum(e["band_skip"] for e in of(events, "decision")) == result.band_skips
    marks = [float(Decimal(e["equity"])) for e in of(events, "mark")]
    assert marks == pytest.approx(result.equity["equity"].to_list()[:-1], rel=1e-12)


def test_an_account_resumes_from_its_ledger(tmp_path: Path) -> None:
    frame = walk(200)
    once = account(tmp_path / "once")
    run(once, frame, config())
    twice = account(tmp_path / "twice")
    run(twice, frame.head(90), config())
    assert twice.state().last_bar == frame["open_time"][88]  # bar 89 waits for its next open
    run(twice, frame, config())  # a later run sees the whole history and carries on

    def strip(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [e for e in events if e["type"] != "open"]

    assert strip(twice.ledger.events()) == strip(once.ledger.events())
    assert run(twice, frame, config()) == []  # nothing new to handle


def test_the_live_next_open_fills_the_latest_decision(tmp_path: Path) -> None:
    frame = walk(60)
    paper = account(tmp_path)
    asked: list[Any] = []

    def next_open(at):  # type: ignore[no-untyped-def]
        asked.append(at)
        return Decimal("123.45")

    handled = run(paper, frame, config(), next_open)
    assert len(handled) == frame.height  # every bar, the last traded at the live open
    assert asked == [frame["open_time"][-1] + DAY]
    last = of(paper.ledger.events(), "decision")[-1]
    assert last["price"] == "123.45"


def test_trading_starts_at_the_terms_and_uses_history_before(tmp_path: Path) -> None:
    frame = walk(120)
    start = frame["open_time"][60]
    terms = Terms("late", "ma-trend", "BTCUSDT", D1, {"fast": 5, "slow": 20}, Decimal(1000), start)
    paper = PaperAccount.open(tmp_path, terms)
    handled = run(paper, frame, config())
    assert handled[0].bar == start
    assert handled[0].equity == Decimal(1000)  # nothing traded before it
    # Its first decision is the strategy on all 61 bars to date, not on a cold start.
    warm = make_strategy("ma-trend", "BTCUSDT", MA).target_weights(frame.head(61))[-1]  # type: ignore[union-attr]
    first = Decimal(of(paper.ledger.events(), "decision")[0]["target"])
    assert first == Decimal(str(warm))


def test_terms_and_state(tmp_path: Path) -> None:
    paper = account(tmp_path)
    assert paper.terms().params == {"fast": 5, "slow": 20}
    state = paper.state()
    assert (state.cash, state.position, state.last_bar, state.last_target) == (
        Decimal(1000),
        0,
        None,
        None,
    )
    with pytest.raises(ValueError, match="already exists"):
        account(tmp_path)
    with pytest.raises(ValueError, match="no paper account"):
        PaperAccount(tmp_path, "missing").terms()
    assert PaperAccount(tmp_path, "missing").ledger.events() == []


def test_a_sim_venue_resumes_but_never_goes_negative() -> None:
    venue = SimVenue(config(), Decimal(1000))
    venue.restore(Decimal(5), {"BTCUSDT": Decimal("0.2")})
    assert (venue.cash(), venue.position("BTCUSDT")) == (Decimal(5), Decimal("0.2"))
    with pytest.raises(ValueError, match="negative"):
        venue.restore(Decimal(-1), {})


def test_rejections_match_the_backtest_too(tmp_path: Path) -> None:
    frame = walk(120)
    tiny = Decimal(4)  # under Binance's 5 USDT minimum order: every buy is refused
    terms = Terms("tiny", "ma-trend", "BTCUSDT", D1, MA, tiny, T0)
    paper = PaperAccount.open(tmp_path, terms)
    run(paper, frame, config())
    result = run_backtest(make_strategy("ma-trend", "BTCUSDT", MA), frame, D1, config(), tiny)  # type: ignore[arg-type]
    rejected = of(paper.ledger.events(), "rejection")
    assert rejected
    assert [(e["at"], e["reason"]) for e in rejected] == [
        (r.time.isoformat(), r.rejection.reason) for r in result.rejections
    ]
    assert of(paper.ledger.events(), "fill") == []


def test_the_ledger_only_writes_what_it_can_read_back(tmp_path: Path) -> None:
    from kestrel.execution.paper import Ledger

    ledger = Ledger(tmp_path / "l.jsonl")
    with pytest.raises(TypeError, match="can't write object"):
        ledger.append({"x": object()})


def test_a_live_run_refuses_to_decide_on_missing_inputs(tmp_path: Path) -> None:
    import polars as pl

    frame = walk(60).with_columns(btc_close=walk(60, seed=11)["close"])
    last = frame["open_time"][-1]
    gappy = frame.with_columns(
        btc_close=pl.when(pl.col("open_time") == last).then(None).otherwise(pl.col("btc_close"))
    )
    paper = account(tmp_path, "btc-regime-trend", {})
    now = last + DAY + timedelta(minutes=10)  # the last bar has just closed
    with pytest.raises(ValueError, match="missing inputs: btc_close on"):
        run(paper, gappy, config(), lambda _: Decimal(100), now=now)
    assert of(paper.ledger.events(), "decision") == []  # nothing written
    handled = run(paper, frame, config(), lambda _: Decimal(100), now=now)
    assert handled  # with the input present it trades
