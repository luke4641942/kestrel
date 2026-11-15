"""The exploration experiment: the split, the lock on the confirmation half, the separate
trial pool, and the CLI paths for exploring and confirming."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kestrel.backtest.engine import run_backtest
from kestrel.backtest.portfolio import run_portfolio
from kestrel.cli import app
from kestrel.config import load_config
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.exploration import (
    ConfirmationLockedError,
    ExplorationError,
    Split,
    check_symbols,
    load_split,
    make_split,
    write_split,
)
from kestrel.research import SECTIONS, ResearchStore
from kestrel.strategy import BuyAndHold
from kestrel.validation.deflated import counts_as_trial, pool_of, trial_set
from tests.backtest.helpers import bars, config
from tests.backtest.test_portfolio import Decisions, panel

REPO = Path(__file__).resolve().parents[1]
SPLIT = Split(date(2026, 11, 16), 1, frozenset({"AAAUSDT"}), frozenset({"BBBUSDT"}))


def test_the_split_pairs_coins_by_volume_one_to_each_half() -> None:
    volumes = {f"C{i}USDT": float(100 - i) for i in range(9)}
    exploration, confirmation = make_split(volumes, seed=7)
    assert make_split(volumes, seed=7) == (exploration, confirmation)  # seeded
    assert set(exploration) | set(confirmation) == set(volumes)
    assert not set(exploration) & set(confirmation)
    assert (len(exploration), len(confirmation)) == (5, 4)  # the odd last coin explores
    assert "C8USDT" in exploration
    ranked = sorted(volumes, key=lambda s: -volumes[s])
    for i in range(0, 8, 2):  # each adjacent pair by volume is split across the halves
        a, b = ranked[i], ranked[i + 1]
        assert (a in exploration) != (b in exploration)


def test_the_split_file_round_trips_and_is_never_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "split.toml"
    assert load_split(path) is None  # no experiment: nothing locked
    write_split(path, SPLIT)
    assert load_split(path) == SPLIT
    with pytest.raises(ExplorationError, match="never rewritten"):
        write_split(path, SPLIT)
    both = Split(date(2026, 11, 16), 1, frozenset({"XUSDT"}), frozenset({"XUSDT"}))
    with pytest.raises(ExplorationError, match="both halves"):
        write_split(tmp_path / "other.toml", both)
    (tmp_path / "bad.toml").write_text("made = 2026-11-16\n")
    with pytest.raises(ExplorationError, match="malformed"):
        load_split(tmp_path / "bad.toml")


def test_the_lock_refuses_only_confirmation_coins_until_unlocked() -> None:
    check_symbols(["AAAUSDT", "BTCUSDT"], SPLIT, False, "x")
    with pytest.raises(ConfirmationLockedError, match="BBBUSDT is in the confirmation half"):
        check_symbols(["aaausdt", "bbbusdt"], SPLIT, False, "x")
    check_symbols(["BBBUSDT"], SPLIT, True, "x")
    check_symbols(["BBBUSDT"], None, False, "x")


def test_the_config_reads_the_split_beside_it_without_changing_its_fingerprint(
    tmp_path: Path,
) -> None:
    shutil.copy(REPO / "kestrel.toml", tmp_path / "kestrel.toml")
    plain = load_config(tmp_path / "kestrel.toml")
    assert plain.split is None
    write_split(tmp_path / "research" / "exploration" / "split.toml", SPLIT)
    loaded = load_config(tmp_path / "kestrel.toml")
    assert loaded.split == SPLIT
    assert loaded.fingerprint() == plain.fingerprint()
    assert not loaded.confirmation_unlocked
    assert loaded.unlock_confirmation().confirmation_unlocked


def test_the_engines_refuse_the_confirmation_half() -> None:
    locked = replace(config(costs=False), split=SPLIT)
    frame = bars([100.0] * 5, step=timedelta(days=1))
    with pytest.raises(ConfirmationLockedError):
        run_backtest(BuyAndHold("BBBUSDT"), frame, Interval.D1, locked, Decimal(1000))
    run_backtest(BuyAndHold("AAAUSDT"), frame, Interval.D1, locked, Decimal(1000))
    run_backtest(
        BuyAndHold("BBBUSDT"), frame, Interval.D1, locked.unlock_confirmation(), Decimal(1000)
    )
    coins = panel({"AAAUSDT": [100.0] * 5, "BBBUSDT": [50.0] * 5})
    with pytest.raises(ConfirmationLockedError):
        run_portfolio(Decisions({0: {"AAAUSDT": 1.0}}), coins, Interval.D1, locked, Decimal(1000))


def meta(confirmation: bool, sharpe: float, a: int) -> dict[str, object]:
    return {
        "strategy": "ma-trend",
        "created_at": "2026-11-16T00:00:00+00:00",
        "params": {"a": a},
        "research": None,
        "symbol": "BBBUSDT",
        "interval": "1d",
        "start": "2020-01",
        "end": "2025-10",
        "initial_cash": "330",
        "used_holdout": False,
        "confirmation": confirmation,
        "metrics": {"sharpe": sharpe},
    }


def test_confirmation_runs_are_their_own_trial_pool() -> None:
    main, confirming = meta(False, 1.0, 1), meta(True, 0.5, 2)
    assert pool_of(main) == "main"
    assert pool_of(confirming) == "confirmation"
    assert counts_as_trial(confirming)  # a trial, of its own pool
    assert not counts_as_trial(confirming, "main")
    assert trial_set([main, confirming]).count == 1
    assert trial_set([main, confirming], pool="confirmation").count == 1
    study = {**meta(True, 0.0, 3), "looks": [{"params": {"b": 1}, "sharpe": 0.2}]}
    assert trial_set([], [study]).count == 0
    assert trial_set([], [study], pool="confirmation").count == 1


def months_of_daily(symbol: str, store: ParquetStore, months: int, volume: float) -> None:
    store.write(
        symbol,
        Interval.D1,
        bars(
            [100.0 + i for i in range(30 * months)],
            start=datetime(2022, 1, 1, tzinfo=UTC),
            step=timedelta(days=1),
            quote_volume=volume,
        ),
    )


def setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A config dir, data and research: four coins with 2+ years, one too young, BTC."""
    data, research = tmp_path / "data", tmp_path / "research"
    shutil.copy(REPO / "kestrel.toml", tmp_path / "kestrel.toml")
    store = ParquetStore(data)
    coins = {"AAAUSDT": 4e7, "BBBUSDT": 3e7, "CCCUSDT": 2e7, "DDDUSDT": 1e7, "BTCUSDT": 9e9}
    for symbol, volume in coins.items():
        months_of_daily(symbol, store, 30, volume)
    months_of_daily("NEWUSDT", store, 6, 5e7)
    (data / "universe").mkdir(parents=True)
    (data / "universe" / "1d.json").write_text(json.dumps({"coins": [*coins, "NEWUSDT"]}))
    return tmp_path / "kestrel.toml", data, research


def cli(*args: str):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(app, list(args))


def test_the_cli_splits_once_explores_apart_and_confirms_only_through_a_note(
    tmp_path: Path,
) -> None:
    cfg, data, research = setup(tmp_path)
    where = ["--data-dir", str(data), "--config", str(cfg)]
    made = cli("exploration-split", *where, "--research-dir", str(research))
    assert made.exit_code == 0, made.output
    assert "4 eligible coins: 2 to exploration, 2 to confirmation" in made.output
    assert cli("exploration-split", *where, "--research-dir", str(research)).exit_code == 1
    split = load_split(tmp_path / "research" / "exploration" / "split.toml")
    assert split is not None
    assert "BTCUSDT" not in split.exploration | split.confirmation  # already searched
    assert "NEWUSDT" not in split.exploration | split.confirmation  # too young
    explore_coin, confirm_coin = sorted(split.exploration)[0], sorted(split.confirmation)[0]

    span = ["--start", "2022-01", "--end", "2023-12", "--interval", "1d", *where]
    span += ["--research-dir", str(research)]
    explored = cli("backtest", explore_coin, "--strategy", "ma-trend", "--explore", *span)
    assert explored.exit_code == 0, explored.output
    assert "not a trial, not evidence" in explored.output
    assert len(list((data / "exploration" / "runs").glob("*/run.json"))) == 1
    assert not (data / "runs").exists()  # nothing in the trial log's store

    assert cli("backtest", confirm_coin, "--explore", *span).exit_code != 0
    locked = cli("backtest", confirm_coin, *span)
    assert locked.exit_code == 1
    assert "confirmation half" in locked.output

    notes = ResearchStore(research)
    plain = notes.create("Not a confirmation").slug
    notes.edit(plain, 1, dict.fromkeys(SECTIONS, "x"), "ma-trend")
    notes.freeze(plain, 1)
    refused = cli("backtest", confirm_coin, "--confirmation", "--research", plain, *span)
    assert refused.exit_code != 0
    graduate = notes.create("Confirmation: ma trend").slug
    assert graduate.startswith("confirmation-")
    notes.edit(graduate, 1, dict.fromkeys(SECTIONS, "x"), "ma-trend")
    notes.freeze(graduate, 1)
    wrong_half = cli("backtest", explore_coin, "--confirmation", "--research", graduate, *span)
    assert wrong_half.exit_code != 0
    confirmed = cli("backtest", confirm_coin, "--confirmation", "--research", graduate, *span)
    assert confirmed.exit_code == 0, confirmed.output
    (saved,) = list((data / "runs").glob("*/run.json"))
    assert json.loads(saved.read_text())["confirmation"] is True


def test_a_confirmation_run_covers_the_half_once_and_counts_as_one_look(tmp_path: Path) -> None:
    from kestrel.validation.log import ValidationLog

    cfg, data, research = setup(tmp_path)
    where = ["--data-dir", str(data), "--config", str(cfg), "--research-dir", str(research)]
    assert cli("exploration-split", *where).exit_code == 0
    notes = ResearchStore(research)
    graduate = notes.create("Confirmation: crash rebound").slug
    notes.edit(graduate, 1, dict.fromkeys(SECTIONS, "x"), "crash-rebound")
    plain = notes.create("Crash rebound, not a confirmation").slug
    for slug in (graduate, plain):
        notes.edit(slug, 1, dict.fromkeys(SECTIONS, "x"), "crash-rebound")
        notes.freeze(slug, 1)

    assert cli("confirm", "--research", plain, *where).exit_code == 1
    ran = cli("confirm", "--research", graduate, *where)
    assert ran.exit_code == 0, ran.output
    assert "2 confirmation coins with 24+ months" in ran.output
    assert "Verdict: FAIL" in ran.output or "Verdict: PASS" in ran.output
    (study,) = ValidationLog(data).studies()
    assert study["kind"] == "confirmation"
    assert study["confirmation"] is True
    assert study["detail"]["note"] == graduate
    assert len(study["detail"]["per_coin"]) == 2
    assert len(study["looks"]) == 1  # the whole run is one look
    assert trial_set([], [study]).count == 0  # not in the main luck bar
    assert trial_set([], [study], pool="confirmation").count == 1
    assert ResearchStore(research).get(graduate).status == "backtested"
    again = cli("confirm", "--research", graduate, *where)
    assert again.exit_code == 1
    assert "never rerun" in again.output


def test_a_coin_too_short_for_a_window_still_counts_for_the_first_test(tmp_path: Path) -> None:
    from kestrel.validation.confirmation import confirm

    store = ParquetStore(tmp_path)
    months_of_daily("LONGUSDT", store, 40, 2e7)
    months_of_daily("SHORTUSDT", store, 26, 2e7)  # 24+ months, but no 24 + 6 window
    cfg = replace(config(costs=False), split=SPLIT)
    result = confirm("crash-rebound", {}, ["LONGUSDT", "SHORTUSDT"], store, cfg, Decimal(330))
    by = {c.symbol: c for c in result.coins}
    assert set(by) == {"LONGUSDT", "SHORTUSDT"}
    assert by["SHORTUSDT"].windows == 0
    assert by["LONGUSDT"].windows > 0
    assert result.windows == by["LONGUSDT"].windows
