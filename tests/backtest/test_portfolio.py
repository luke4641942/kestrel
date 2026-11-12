"""The portfolio engine, on hand-built daily panels.

Each bar trades at one price (open = high = low = close), so the estimated spread is zero
and, with zero costs, every number can be checked by hand. Prices move between bars."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import polars as pl
import pytest

from kestrel.backtest.engine import HoldoutError
from kestrel.backtest.portfolio import run_portfolio, validated_decisions
from kestrel.data.panel import with_histories
from kestrel.data.schema import Interval
from kestrel.strategy import LookaheadError
from tests.backtest.helpers import config

D1 = Interval.D1
DAY = timedelta(days=1)
T0 = datetime(2024, 1, 1, tzinfo=UTC)


def panel(
    prices: dict[str, list[float | None]], start: datetime = T0, volume: float = 1e9
) -> pl.DataFrame:
    """One bar per day per coin; None leaves that day out (a gap or a delisting)."""
    rows = [
        {
            "symbol": s,
            "open_time": start + i * DAY,
            "open": p,
            "high": p,
            "low": p,
            "close": p,
            "quote_volume": volume,
        }
        for s, ps in prices.items()
        for i, p in enumerate(ps)
        if p is not None
    ]
    return with_histories(pl.DataFrame(rows), D1)


class Decisions:
    """Replays fixed decisions: day index -> {symbol: weight} ({} holds nothing)."""

    name = "fixed decisions"

    def __init__(self, decisions: dict[int, dict[str, float]], start: datetime = T0) -> None:
        self.decisions, self.start = decisions, start

    def target_weights(self, p: pl.DataFrame) -> pl.DataFrame:
        last = p["open_time"].max()
        rows: list[dict[str, object]] = []
        for i, weights in self.decisions.items():
            when = self.start + i * DAY
            if last is None or when > last:
                continue
            rows += [{"open_time": when, "symbol": s, "weight": w} for s, w in weights.items()]
            if not weights:
                rows.append({"open_time": when, "symbol": None, "weight": 0.0})
        return pl.DataFrame(
            rows,
            schema={"open_time": pl.Datetime("us", "UTC"), "symbol": pl.Utf8, "weight": pl.Float64},
        )


def run(
    p: pl.DataFrame, decisions: dict[int, dict[str, float]], costs: bool = False, band: float = 0.02
):  # type: ignore[no-untyped-def]
    return run_portfolio(Decisions(decisions), p, D1, config(costs=costs, band=band), Decimal(1000))


def test_a_decision_fills_at_the_next_open_and_drifts_until_the_next() -> None:
    # Decide at day 0's close: half A, half B. Buy at day 1's prices: 5 A at 100, 10 B at 50.
    p = panel({"A": [100, 100, 110, 121], "B": [50, 50, 50, 25]})
    r = run(p, {0: {"A": 0.5, "B": 0.5}})
    assert [f.fill.quantity for f in r.fills] == [Decimal(5), Decimal(10)]
    assert r.fills[0].time == T0 + DAY
    assert r.equity["equity"].to_list() == pytest.approx([1000, 1000, 1050, 855])  # 5x121 + 10x25
    assert len(r.fills) == 2  # weights drifted, but nothing trades between decisions
    assert r.symbol == "UNIVERSE"


def test_a_coin_dropped_from_the_target_is_sold_in_full() -> None:
    p = panel({"A": [100] * 5, "B": [50] * 5})
    r = run(p, {0: {"A": 0.5, "B": 0.5}, 2: {"A": 0.5}})
    sides = [(f.fill.symbol, f.fill.side.value) for f in r.fills]
    assert sides == [("A", "buy"), ("B", "buy"), ("B", "sell")]
    assert r.equity["cash"][-1] == pytest.approx(500)


def test_resizing_inside_the_band_is_skipped_and_counted() -> None:
    p = panel({"A": [100] * 5})
    r = run(p, {0: {"A": 0.5}, 2: {"A": 0.51}})
    assert len(r.fills) == 1
    assert r.band_skips == 1


def test_a_decision_to_hold_nothing_sells_everything() -> None:
    p = panel({"A": [100] * 5, "B": [50] * 5})
    r = run(p, {0: {"A": 0.5, "B": 0.5}, 2: {}})
    assert [f.fill.side.value for f in r.fills] == ["buy", "buy", "sell", "sell"]
    assert r.equity["weight"][-1] == 0


def test_sells_fund_buys_on_a_rebalance() -> None:
    # Fully in A, then fully in B: B can only be bought with A's proceeds.
    p = panel({"A": [100] * 5, "B": [50] * 5})
    r = run(p, {0: {"A": 1.0}, 2: {"B": 1.0}})
    assert [(f.fill.symbol, f.fill.side.value) for f in r.fills] == [
        ("A", "buy"),
        ("A", "sell"),
        ("B", "buy"),
    ]
    assert r.equity["equity"][-1] == pytest.approx(1000)


def test_a_coin_whose_bars_stop_is_sold_at_its_last_close() -> None:
    # A is delisted after day 2 (last close 80); on day 3 the engine sells it at 80.
    p = panel({"A": [100, 100, 80, None, None], "B": [50] * 5})
    r = run(p, {0: {"A": 0.5, "B": 0.5}})
    exit_fill = r.fills[-1]
    assert (exit_fill.fill.symbol, exit_fill.fill.side.value) == ("A", "sell")
    assert exit_fill.fill.reference_price == Decimal(80)
    assert exit_fill.time == T0 + 3 * DAY  # the close of its last bar
    assert r.forced_exits == 1
    assert r.equity["equity"][-1] == pytest.approx(400 + 500)


def test_each_coin_pays_the_spread_of_its_volume_band() -> None:
    # A trades 2 billion USDT a day (the 1 bp band); B trades 50 million (8 bps).
    p = pl.concat([panel({"A": [100] * 40}), panel({"B": [50] * 40}, volume=5e7)]).sort(
        "symbol", "open_time"
    )
    r = run(p, {35: {"A": 0.4, "B": 0.4}}, costs=True)
    by = {f.fill.symbol: f.fill for f in r.fills}
    for symbol, half in (("A", 0.0001), ("B", 0.0008)):
        fill = by[symbol]
        assert float(fill.spread_cost / (fill.quantity * fill.reference_price)) == pytest.approx(
            half
        )


def test_the_holdout_is_refused() -> None:
    p = panel({"A": [100] * 5}, start=datetime(2025, 10, 30, tzinfo=UTC))
    with pytest.raises(HoldoutError):
        run(p, {0: {"A": 1.0}})


@pytest.mark.parametrize(
    ("decisions", "message"),
    [
        ({0: {"A": 0.7, "B": 0.6}}, "sum above 1"),
        ({0: {"A": -0.1}}, r"\[0, 1\]"),
        ({0: {"C": 0.5}}, "no bar"),
    ],
)
def test_bad_decisions_are_refused(decisions: dict[int, dict[str, float]], message: str) -> None:
    p = panel({"A": [100] * 5, "B": [50] * 5})
    with pytest.raises(ValueError, match=message):
        validated_decisions(Decisions(decisions), p)


def test_bad_columns_and_missing_weights_are_refused() -> None:
    p = panel({"A": [100] * 5})

    class Wrong:
        name = "wrong"

        def __init__(self, frame: pl.DataFrame) -> None:
            self.frame = frame

        def target_weights(self, _: pl.DataFrame) -> pl.DataFrame:
            return self.frame

    with pytest.raises(ValueError, match="columns"):
        validated_decisions(Wrong(pl.DataFrame({"open_time": [T0]})), p)
    nan = pl.DataFrame({"open_time": [T0], "symbol": ["A"], "weight": [float("nan")]})
    with pytest.raises(ValueError, match="NaN"):
        validated_decisions(Wrong(nan), p)


def test_a_strategy_that_peeks_at_the_future_is_refused() -> None:
    class Peeks:
        """Holds whichever coin ends highest: knowable only at the end."""

        name = "peeks"

        def target_weights(self, p: pl.DataFrame) -> pl.DataFrame:
            last = p.filter(pl.col("open_time") == p["open_time"].max())
            best = last.sort("close", descending=True)["symbol"][0]
            days = p["open_time"].unique().sort()
            return pl.DataFrame(
                {"open_time": days, "symbol": [best] * days.len(), "weight": [1.0] * days.len()}
            )

    p = panel(
        {
            "A": [100 + i for i in range(30)],
            "B": [100 + (2 * i if i > 20 else 0) for i in range(30)],
        }
    )
    with pytest.raises(LookaheadError, match="information from the future"):
        validated_decisions(Peeks(), p)


def test_a_forced_sale_the_exchange_refuses_keeps_the_coin_at_its_last_close() -> None:
    # 3% in A buys 0.3 A at 100 (30 USDT; a smaller weight would sit inside the band and
    # never be bought). A falls to 10, so the position is worth 3 USDT, under the 5 USDT
    # minimum, then its bars stop: the forced sale is refused, recorded, and the coin is
    # carried at its last close.
    p = panel({"A": [100, 100, 10, None, None], "B": [50] * 5})
    r = run(p, {0: {"A": 0.03, "B": 0.5}})
    assert r.forced_exits == 0
    assert "below the minimum" in r.rejections[0].rejection.reason
    assert r.equity["equity"][-1] == pytest.approx(0.3 * 10 + 10 * 50 + 470)


def test_a_buy_with_no_cash_left_is_skipped() -> None:
    # Fully invested with no cash. The next decision trims A and C by 1.5 points each
    # (inside the 2-point band, so neither is sold) and raises B by 3: there is no cash
    # to buy with, so nothing happens rather than an order for nothing.
    p = panel({"A": [100] * 5, "B": [100] * 5, "C": [100] * 5})
    r = run(p, {0: {"A": 0.485, "B": 0.03, "C": 0.485}, 2: {"A": 0.47, "B": 0.06, "C": 0.47}})
    assert len(r.fills) == 3
    assert r.equity["cash"][2] == 0
    assert r.rejections == []
    assert r.band_skips == 2


def test_duplicate_bars_are_refused_not_repaired() -> None:
    # Binance's archive has a few exact duplicate days (AXSUSDT 2026-02-10). Keeping one
    # copy would be a silent repair; the engine refuses and names the coin.
    p = panel({"A": [100] * 5, "B": [50] * 5})
    doubled = pl.concat([p, p.filter((pl.col("symbol") == "B") & (pl.col("open_time") == T0))])
    with pytest.raises(ValueError, match="duplicate bars for B"):
        run(doubled, {0: {"A": 0.5}})
