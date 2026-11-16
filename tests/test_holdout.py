"""The holdout test, on synthetic bars only: the real holdout is read once per note, from
the CLI, after Luke freezes it. The test config's holdout starts 2025-11-01."""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kestrel.cli import app
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.research import ResearchStore
from kestrel.validation.holdout import HoldoutLog, HoldoutResult, Scored, _score, holdout_test
from tests.backtest.helpers import bars, config

D1 = Interval.D1
START = datetime(2025, 11, 1, tzinfo=UTC)
END = datetime(2026, 11, 1, tzinfo=UTC)
REPO_CONFIG = str(Path(__file__).resolve().parents[1] / "kestrel.toml")


def daily(seed: int = 2, drift: float = 0.002):  # type: ignore[no-untyped-def]
    """Daily bars from 2025-05-01 to 2026-10-31."""
    rnd = random.Random(seed)
    n = (END - datetime(2025, 5, 1, tzinfo=UTC)).days
    closes, price = [], 100.0
    for _ in range(n):
        price *= math.exp(rnd.gauss(drift, 0.03))
        closes.append(price)
    return bars(
        [closes[0], *closes[:-1]],
        closes=closes,
        start=datetime(2025, 5, 1, tzinfo=UTC),
        step=timedelta(days=1),
    )


def run(frame=None, **over):  # type: ignore[no-untyped-def]
    kwargs = {"start": START, "end": END, **over}
    return holdout_test(
        "ma-trend", "BTCUSDT", daily() if frame is None else frame, D1, config(costs=False),
        Decimal(1000), {"fast": 5, "slow": 20}, **kwargs,
    )  # fmt: skip


def test_only_the_holdout_days_are_scored_against_holding() -> None:
    frame = daily()
    r = run(frame)
    closes = dict(zip(frame["open_time"].to_list(), frame["close"].to_list(), strict=True))
    # Holding over the scored days: from the close before 2025-11-01 to the last close.
    first, last = closes[START - timedelta(days=1)], closes[END - timedelta(days=1)]
    # Within rounding: buy-and-hold buys a whole number of 0.00001 BTC steps, so a few cents
    # stay in cash and its equity isn't exactly proportional to the price.
    assert r.held.total_return == pytest.approx(last / first - 1, rel=1e-5)
    assert 0 < r.exposure < 1
    assert r.matched_return != r.held.total_return
    assert r.held.sharpe is not None


def test_scoring_and_drawdown() -> None:
    s = _score([0.1, -0.5, 0.2])
    assert s.total_return == pytest.approx(1.1 * 0.5 * 1.2 - 1)
    assert s.max_drawdown == pytest.approx(-0.5)


def result(mine: Scored, held: Scored, matched: float = 0.0) -> HoldoutResult:
    return HoldoutResult("x", "BTCUSDT", {}, START, END, 0.5, mine, held, matched)


def test_the_edge_rule_compares_sharpes_in_a_rising_year() -> None:
    up = Scored(1.0, 0.4, -0.2)
    assert result(Scored(1.0, 0.1, -0.1), up).edge_passes  # level counts
    assert not result(Scored(0.9, 0.9, -0.1), up).edge_passes  # more return, less Sharpe
    assert not result(Scored(None, 0.0, 0.0), up).edge_passes  # never invested


def test_in_a_falling_year_it_compares_returns_at_the_same_exposure() -> None:
    down = Scored(-1.5, -0.5, -0.6)
    lost_less = Scored(-2.0, -0.1, -0.15)  # a worse Sharpe, but far smaller loss
    r = result(lost_less, down, matched=-0.25)
    assert r.falling_year
    assert r.edge_passes
    assert not result(Scored(-0.5, -0.3, -0.3), down, matched=-0.25).edge_passes
    assert result(Scored(0.5, 0.1, -0.1), Scored(None, 0.0, 0.0), matched=0.0).falling_year


def test_the_drawdown_limit() -> None:
    up = Scored(1.0, 0.4, -0.2)
    assert result(Scored(2.0, 0.5, -0.5), up).passes
    deep = result(Scored(2.0, 0.5, -0.51), up)
    assert deep.edge_passes
    assert not deep.drawdown_passes
    assert not deep.passes


def test_it_scores_the_holdout_only_and_needs_a_warm_up() -> None:
    with pytest.raises(ValueError, match="scores the holdout only"):
        run(start=datetime(2025, 10, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="must start before"):
        run(daily().filter(daily()["open_time"] >= START))


def note(root: Path, name: str = "Holdout: a test", frozen: bool = True) -> str:
    store = ResearchStore(root)
    s = store.create(name)
    sections = ("hypothesis", "rationale", "rules", "falsification", "parameters", "costs")
    store.edit(s.slug, 1, dict.fromkeys(sections, "x"), "ma-trend")
    if frozen:
        store.freeze(s.slug, 1)
    return s.slug


def cli(tmp_path: Path, slug: str, strategy: str = "ma-trend"):  # type: ignore[no-untyped-def]
    args = [
        "holdout-test", "BTCUSDT", "--strategy", strategy, "--research", slug,
        "--warmup-start", "2025-05", "--start", "2025-11", "--end", "2026-10",
        "--data-dir", str(tmp_path), "--research-dir", str(tmp_path / "r"), "--config", REPO_CONFIG,
    ]  # fmt: skip
    return CliRunner().invoke(app, args)


def test_the_cli_runs_once_per_note_and_records_it(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", D1, daily())
    slug = note(tmp_path / "r")
    first = cli(tmp_path, slug)
    assert first.exit_code == 0, first.output
    assert "HOLDOUT USED: ma-trend on BTCUSDT" in first.output
    assert "HOLDOUT TEST" in first.output
    saved = json.loads((tmp_path / "holdout" / f"{slug}.json").read_text())
    assert saved["research"] == slug
    assert saved["warmup_start"].startswith("2025-05-01")
    assert saved["passes"] in (True, False)
    assert ResearchStore(tmp_path / "r").get(slug).status == "backtested"
    again = cli(tmp_path, slug)
    assert again.exit_code == 1
    assert "already used the holdout" in again.output


def test_the_cli_refuses_unfrozen_or_mismatched_notes(tmp_path: Path) -> None:
    ParquetStore(tmp_path).write("BTCUSDT", D1, daily())
    draft = note(tmp_path / "r", frozen=False)
    assert cli(tmp_path, draft).exit_code == 1
    frozen = note(tmp_path / "r", name="Holdout: another")
    wrong = cli(tmp_path, frozen, strategy="buy-and-hold")
    assert wrong.exit_code == 1
    assert "names ma-trend, not buy-and-hold" in wrong.output
    assert not (tmp_path / "holdout").exists()  # nothing recorded for refused runs


def test_the_log_refuses_a_second_record_and_lists_them(tmp_path: Path) -> None:
    log = HoldoutLog(tmp_path)
    assert log.records() == []
    r = result(Scored(1.0, 0.1, -0.1), Scored(0.5, 0.1, -0.2))
    log.record("a-note", 1, r, START - timedelta(days=184))
    with pytest.raises(ValueError, match="used once"):
        log.record("a-note", 1, r, START)
    (saved,) = log.records()
    assert (saved["research"], saved["passes"]) == ("a-note", True)


def test_no_bars_in_the_scored_period() -> None:
    early = daily().filter(daily()["open_time"] < START)
    with pytest.raises(ValueError, match="no stored bars in the scored period"):
        run(early)


def test_the_api_lists_records_and_cannot_run_one(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kestrel.api.app import create_app

    client = TestClient(create_app(tmp_path, static_dir=None))
    assert client.get("/api/holdout").json() == []
    log = HoldoutLog(tmp_path)
    log.record("older", 1, result(Scored(1.0, 0.1, -0.1), Scored(0.5, 0.1, -0.2)), START)
    falling = result(Scored(-2.0, -0.1, -0.15), Scored(-1.5, -0.5, -0.6), matched=-0.25)
    log.record("newer", 1, falling, START)
    listed = client.get("/api/holdout").json()
    assert [r["research"] for r in listed] == ["newer", "older"]
    assert listed[0]["falling_year"] is True
    assert listed[0]["edge_passes"] is True
    assert listed[0]["mine"]["total_return"] == pytest.approx(-0.1)
    # Read-only: no route runs a holdout test.
    assert client.post("/api/holdout", json={}).status_code == 405
    paths = [getattr(r, "path", "") for r in client.app.routes]  # type: ignore[attr-defined]
    assert not [p for p in paths if "holdout" in p and "jobs" in p]


def test_a_coin_basket_is_scored_against_btc_held(tmp_path: Path) -> None:
    from kestrel.data.universe import manifest_path

    store = ParquetStore(tmp_path)
    coins = {"BTCUSDT": 2, "ETHUSDT": 3, "SOLUSDT": 4}
    for name, seed in coins.items():
        store.write(name, D1, daily(seed=seed))
    path = manifest_path(store, D1)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"coins": dict.fromkeys(coins, 18)}))
    research = ResearchStore(tmp_path / "r")
    s = research.create("Holdout: rotation")
    sections = ("hypothesis", "rationale", "rules", "falsification", "parameters", "costs")
    research.edit(s.slug, 1, dict.fromkeys(sections, "x"), "monthly-rotation")
    research.freeze(s.slug, 1)
    args = [
        "holdout-test", "UNIVERSE", "--strategy", "monthly-rotation", "--research", s.slug,
        "--warmup-start", "2025-05", "--start", "2025-11", "--end", "2026-10",
        "--param", "lookback_days=30", "--param", "trend_days=50",
        "--data-dir", str(tmp_path), "--research-dir", str(tmp_path / "r"), "--config", REPO_CONFIG,
    ]  # fmt: skip
    got = CliRunner().invoke(app, args)
    assert got.exit_code == 0, got.output
    assert "HOLDOUT USED: monthly-rotation on UNIVERSE" in got.output
    saved = json.loads((tmp_path / "holdout" / f"{s.slug}.json").read_text())
    assert saved["params"]["lookback_days"] == 30
    assert saved["symbol"] == "UNIVERSE"


def test_only_a_confirmation_note_can_hold_out_test_the_confirmation_half(tmp_path: Path) -> None:
    import shutil
    from datetime import date

    from kestrel.exploration import Split, write_split

    shutil.copy(REPO_CONFIG, tmp_path / "kestrel.toml")
    write_split(
        tmp_path / "research" / "exploration" / "split.toml",
        Split(date(2026, 11, 16), 1, frozenset({"OTHERUSDT"}), frozenset({"LOCKEDUSDT"})),
    )
    ParquetStore(tmp_path).write("LOCKEDUSDT", D1, daily())

    def run_on_locked(slug: str):  # type: ignore[no-untyped-def]
        args = [
            "holdout-test", "LOCKEDUSDT", "--strategy", "ma-trend", "--research", slug,
            "--warmup-start", "2025-05", "--start", "2025-11", "--end", "2026-10",
            "--data-dir", str(tmp_path), "--research-dir", str(tmp_path / "r"),
            "--config", str(tmp_path / "kestrel.toml"),
        ]  # fmt: skip
        return CliRunner().invoke(app, args)

    plain = run_on_locked(note(tmp_path / "r", name="Holdout: plain"))
    assert plain.exit_code == 1
    assert "confirmation half" in plain.output
    confirming = run_on_locked(note(tmp_path / "r", name="Confirmation: on the locked half"))
    assert confirming.exit_code == 0, confirming.output
    assert "HOLDOUT TEST" in confirming.output
