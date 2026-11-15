"""The cost gate: S2 setups' stop widths, what they cost in R, and the verdict.

Uses the hand-built sequence from tests/test_structure.py, with swing_bars=1:

    bar:   0    1    2    3    4    5    6    7    8    9
    high: 120  125  119  112  116  111  109  121  128  126
    low:  115  118  110  105  108  100  102  107  117  110

Swing highs 125 (bar 1) then a lower 116 (bar 4); bar 7 closes at 120, above it: a change
of character. The leg's low is 100 (bar 5). Bars 6 and 8 leave a gap from 109 to 117:
entry at its midpoint 113, stop at bar 6's low, 102.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.studies.cost_gate import (
    RECLAIM_BARS,
    Gate,
    Setup,
    break_even_win_rate,
    format_gates,
    in_session,
    load_all,
    measure,
    order_flow_setups,
    path,
    save,
    setups,
    value_area,
)
from tests.backtest.helpers import T0, bars
from tests.test_structure import CLOSE, HIGH, LOW, frame


def minutes(start: datetime = T0):  # type: ignore[no-untyped-def]
    return bars(
        [CLOSE[0], *CLOSE[:-1]],
        closes=CLOSE,
        highs=HIGH,
        lows=LOW,
        start=start,
        step=timedelta(minutes=1),
    )


REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")


def test_the_setup_is_the_gap_after_the_change_of_character() -> None:
    (found,) = setups(frame(), swing_bars=1, atr_bars=1)
    assert found.at == frame()["open_time"][7]
    assert (found.entry, found.stop) == (113.0, 102.0)
    assert found.width_bps == pytest.approx(11 / 113 * 1e4)


def test_a_gap_smaller_than_the_atr_filter_is_no_setup() -> None:
    # Bar 8's true range is 11, so the 8-point gap needs min_gap_atr <= 8/11.
    assert setups(frame(), swing_bars=1, atr_bars=1, min_gap_atr=0.7)
    assert setups(frame(), swing_bars=1, atr_bars=1, min_gap_atr=0.75) == []
    assert setups(frame(), swing_bars=1) == []  # ATR(14) isn't known on 10 bars


def test_break_even_win_rate() -> None:
    assert break_even_win_rate(4, 0.0) == pytest.approx(0.2)
    assert break_even_win_rate(4, 0.5) == pytest.approx(0.3)
    assert break_even_win_rate(2, 0.73) == pytest.approx(1.73 / 3)


def test_the_verdict_is_on_the_median_setup() -> None:
    assert Gate("x", 22.0, [88.0, 100.0, 200.0]).verdict == "CARRIES"  # 0.22R
    assert Gate("x", 22.0, [10.0, 60.0, 500.0]).verdict == "MARGINAL"  # 0.37R
    gate = Gate("x", 22.0, [20.0, 30.0, 40.0])
    assert gate.verdict == "FAILS"
    assert gate.median_cost_r == pytest.approx(22 / 30)
    assert gate.share_over_half_r == 1.0
    assert Gate("x", 22.0, []).verdict == "NO SETUPS"


def test_the_new_york_open_window() -> None:
    def at(h: int, m: int) -> datetime:
        return datetime(2025, 3, 3, h, m, tzinfo=UTC)

    assert in_session(at(13, 30))
    assert in_session(at(15, 59))
    assert not in_session(at(12, 59))
    assert not in_session(at(16, 0))
    found = [Setup(at(14, 0), 100.0, 99.0), Setup(at(3, 0), 100.0, 99.5)]
    assert measure("BTC 1m", found, 22.0).count == 2
    ny = measure("BTC 1m", found, 22.0, ny_only=True)
    assert (ny.market, ny.widths_bps) == ("BTC 1m, NY open", [pytest.approx(100.0)])
    report = format_gates([ny])
    assert "22 bp round trip" in report
    assert "0.22R" in report
    assert "CARRIES" in report
    tight = format_gates([Gate("x", 22.0, [5.0])])  # 4.4R a trade: 2R can never cover it
    assert "never" in tight
    assert "FAILS" in tight


def test_the_command(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    store.write("BTCUSDT", Interval.M1, minutes())
    args = ["cost-gate", "BTCUSDT", "--data-dir", str(tmp_path), "--config", REPO_CONFIG]
    got = CliRunner().invoke(app, args)
    assert got.exit_code == 0, got.output
    assert "BTCUSDT 1m: 10 bars" in got.output
    assert "NO SETUPS" in got.output  # ATR(14) needs more than 10 bars
    missing = CliRunner().invoke(app, [*args[:1], "ETHUSDT", *args[2:]])
    assert missing.exit_code == 1
    assert "no stored ETHUSDT 1m bars" in missing.output


def test_the_command_never_reads_the_holdout(tmp_path: Path) -> None:
    late = minutes(datetime(2025, 11, 1, tzinfo=UTC) - timedelta(minutes=3))
    ParquetStore(tmp_path).write("BTCUSDT", Interval.M1, late)
    got = CliRunner().invoke(
        app, ["cost-gate", "BTCUSDT", "--data-dir", str(tmp_path), "--config", REPO_CONFIG]
    )
    assert got.exit_code == 0, got.output
    assert "BTCUSDT 1m: 3 bars" in got.output  # the 7 bars from 2025-11-01 are left out


def test_the_command_saves_the_gate_for_the_dashboard(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kestrel.api.app import create_app

    ParquetStore(tmp_path).write("BTCUSDT", Interval.M1, minutes())
    args = ["cost-gate", "BTCUSDT", "--data-dir", str(tmp_path), "--config", REPO_CONFIG]
    got = CliRunner().invoke(app, args)
    assert got.exit_code == 0, got.output
    assert "Saved for the dashboard" in got.output
    client = TestClient(create_app(tmp_path, static_dir=None, config_path=Path(REPO_CONFIG)))
    (gate,) = client.get("/api/cost-gate").json()
    assert gate["interval"] == "1m"
    assert gate["round_trip_bps"] == 17.0
    assert [r["market"] for r in gate["rows"]] == ["BTCUSDT 1m, all hours", "BTCUSDT 1m, NY open"]
    assert gate["rows"][0]["verdict"] == "NO SETUPS"
    assert gate["setup"] == "fvg"
    assert gate["rows"][0]["target_r"] is None
    t = datetime(2025, 1, 1, tzinfo=UTC)
    flow = Gate("order flow BTCUSDT 1m, all hours", 17.0, [34.0], [2.5])
    save([flow], "1m", t, t, tmp_path, "order-flow")
    both = client.get("/api/cost-gate").json()
    assert sorted(g["setup"] for g in both) == ["fvg", "order-flow"]
    (saved,) = [g for g in both if g["setup"] == "order-flow"]
    assert saved["rows"][0]["target_r"] == 2.5


def test_saved_gates_keep_their_numbers_and_sort_by_bar_size(tmp_path: Path) -> None:
    t = datetime(2025, 1, 1, tzinfo=UTC)
    save([Gate("SOLUSDT 5m, NY open", 22.0, [40.0, 45.0, 50.0])], "5m", t, t, tmp_path)
    save([Gate("BTCUSDT 1m, all hours", 22.0, [5.0])], "1m", t, t, tmp_path)
    first, second = load_all(tmp_path)
    assert (first["interval"], second["interval"]) == ("1m", "5m")
    row = second["rows"][0]
    assert row["median_bps"] == 45.0
    assert row["cost_r"] == pytest.approx(22 / 45)
    assert row["break_even"]["4"] == pytest.approx((1 + 22 / 45) / 5)
    assert row["verdict"] == "MARGINAL"
    assert load_all(tmp_path / "none") == []


def session(
    lows: list[float],
    closes: list[float],
    *,
    start: datetime,
    buyers: list[bool] | None = None,
    volume: float = 10.0,
):  # type: ignore[no-untyped-def]
    """1m bars with chosen lows and closes; opens and highs at the close, so a bar's typical
    price sits near it. ``buyers``: whether takers bought more than half the volume."""
    frame = bars(
        closes,
        closes=closes,
        highs=closes,
        lows=lows,
        start=start,
        step=timedelta(minutes=1),
    )
    flags = buyers or [True] * len(closes)
    return frame.with_columns(
        volume=pl.lit(volume),
        taker_buy_base_volume=pl.Series([volume * (0.7 if b else 0.3) for b in flags]),
    )


DAY = datetime(2024, 3, 1, tzinfo=UTC)


def test_the_value_area_holds_seventy_percent_around_the_busiest_price() -> None:
    closes = [100.0] * 60 + [100.2] * 20 + [99.8] * 20  # bins of 5 bps of 100: 0.05 wide
    area = value_area(session(closes, closes, start=DAY))
    assert area is not None
    val, poc, vah = area
    assert 100.0 <= poc <= 100.05  # the busiest bin (100.0's)
    assert val <= 100.0 < vah  # 60% is in the POC bin, so one neighbour more makes 80%
    assert vah - val < 0.5


def previous_day():  # type: ignore[no-untyped-def]
    closes = [100.0] * 80 + [99.0] * 10 + [101.0] * 10
    return session(closes, closes, start=DAY - timedelta(days=1))


def test_a_failed_breakdown_reclaimed_by_buyers_is_a_setup() -> None:
    before = previous_day()
    area = value_area(before)
    assert area is not None
    val, poc, _ = area
    lows = [100.0, val - 0.3, val - 0.5, val - 0.1, val - 0.05]
    closes = [100.0, val - 0.2, val - 0.3, val - 0.02, val + 0.02]
    found = order_flow_setups(pl.concat([before, session(lows, closes, start=DAY)]))
    assert len(found) == 1
    assert found[0].entry == pytest.approx(val + 0.02)
    assert found[0].stop == pytest.approx(val - 0.5)  # the breakdown's lowest low
    assert found[0].at == DAY + timedelta(minutes=4)
    assert poc > found[0].entry
    assert found[0].target == poc
    target_r = found[0].target_r
    assert target_r is not None
    assert target_r == pytest.approx((poc - found[0].entry) / (found[0].entry - found[0].stop))


def test_no_setup_without_buyers_too_late_or_with_nothing_to_aim_at() -> None:
    before = previous_day()
    area = value_area(before)
    assert area is not None
    val, poc, _ = area
    sellers = session(
        [val - 0.3, val - 0.1], [val - 0.2, val + 0.02], start=DAY, buyers=[True, False]
    )
    assert order_flow_setups(pl.concat([before, sellers])) == []
    slow = [val - 0.2] * RECLAIM_BARS + [val + 0.02]
    late = session([val - 0.3] * len(slow), slow, start=DAY)
    assert order_flow_setups(pl.concat([before, late])) == []
    above_poc = session([val - 0.3, val - 0.1], [val - 0.2, poc + 0.1], start=DAY)
    assert order_flow_setups(pl.concat([before, above_poc])) == []


def test_the_order_flow_gate_saves_apart_from_the_fvg_gate(tmp_path: Path) -> None:
    assert path(tmp_path, "1m").name == "1m.json"
    assert path(tmp_path, "1m", "order-flow").name == "order-flow-1m.json"
    ParquetStore(tmp_path).write("BTCUSDT", Interval.M1, minutes())
    args = ["cost-gate", "BTCUSDT", "--setup", "order-flow", "--data-dir", str(tmp_path)]
    result = CliRunner().invoke(app, [*args, "--config", REPO_CONFIG])
    assert result.exit_code == 0, result.output
    assert "order flow BTCUSDT 1m" in result.output
    assert "order flow Model 2 stops" in result.output
    assert (tmp_path / "cost_gate" / "order-flow-1m.json").exists()
    assert not (tmp_path / "cost_gate" / "1m.json").exists()
    bad = CliRunner().invoke(
        app, ["cost-gate", "BTCUSDT", "--setup", "nope", "--data-dir", str(tmp_path)]
    )
    assert bad.exit_code == 1


def test_the_report_shows_a_setups_own_target_and_its_break_even() -> None:
    gate = Gate("order flow X 1m, all hours", 17.0, [34.0, 34.0], [1.0, 1.0])
    text = format_gates([gate], "order-flow")
    assert "order flow Model 2 stops" in text
    assert "1.00R   break-even win rate 75%" in text  # (1 + 0.5) / (1 + 1)
    assert "point of control" in text
    assert "own target" not in format_gates([Gate("X", 17.0, [34.0])])
