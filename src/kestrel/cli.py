"""Command-line entry point.

kestrel ingest BTCUSDT --interval 1h --start 2020-01
kestrel ingest-universe --interval 1d --start 2020-01
kestrel ingest-sentiment
kestrel ingest-funding BTCUSDT ETHUSDT
kestrel check BTCUSDT --interval 1h
kestrel list BTCUSDT --interval 1h
kestrel backtest BTCUSDT --interval 4h --start 2022-01 --end 2025-10
kestrel anchor-study BTCUSDT --start 2020-01 --end 2025-10
kestrel walk-forward BTCUSDT --strategy ma-trend --grid fast=10,20 --grid slow=50,100 ...
kestrel robustness BTCUSDT --strategy channel-breakout --interval 4h --start ... --end ...
kestrel random-entry BTCUSDT --strategy structure-pullback --interval 4h --start ... --end ...
kestrel cost-gate BTCUSDT ETHUSDT SOLUSDT --interval 1m
"""

from __future__ import annotations

import json
import math
import plistlib
import shlex
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import httpx
import polars as pl
import typer

from kestrel.backtest.costs import CostModel
from kestrel.backtest.engine import BacktestResult, HoldoutError, run_backtest
from kestrel.backtest.metrics import Metrics, compute_metrics, format_report
from kestrel.backtest.portfolio import run_portfolio
from kestrel.backtest.runs import (
    UNIVERSE_BENCHMARK,
    RunRequest,
    RunStore,
    execute,
    strategy_panel,
)
from kestrel.config import Config, ConfigError, load_config
from kestrel.data import attention, delistings, funding, live, news, sentiment, stablecoins
from kestrel.data.binance_vision import BinanceVisionClient, Month, last_complete_month
from kestrel.data.features import attach_features, markets_for
from kestrel.data.quality import check_klines
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.data.universe import ArchiveListing, CoinIngest, ingest_universe, manifest_path
from kestrel.execution import paper as paper_trading
from kestrel.execution import safety, tracking
from kestrel.execution.binance import (
    TESTNET_URL,
    BinanceClient,
    BinanceError,
    BinanceVenue,
    Credentials,
)
from kestrel.execution.venue import SimVenue
from kestrel.exploration import (
    CONFIRMATION_PREFIX,
    MIN_MONTHS,
    SEARCHED,
    SEED,
    ConfirmationLockedError,
    Split,
    make_split,
    write_split,
)
from kestrel.regimes import format_regimes
from kestrel.regimes import walk_forward as walk_regimes
from kestrel.research import ResearchStore, run_target
from kestrel.strategy import (
    STRATEGIES,
    UNIVERSE,
    BracketStrategy,
    BuyAndHold,
    make_portfolio_strategy,
    make_strategy,
    resolve_params,
    spec_for,
)
from kestrel.studies import cost_gate
from kestrel.studies.anchor_day import format_study, run_study
from kestrel.studies.delisting import (
    format_delisting_study,
    measure_all,
    run_delisting_study,
)
from kestrel.studies.delisting_short import (
    ShortPath,
    format_short_study,
    ingest_perps,
    load_perp,
    perp_symbols,
    run_short_study,
    short_path,
)
from kestrel.studies.volatile_direction import format_study as format_volatile_study
from kestrel.studies.volatile_direction import run_study as run_volatile_study
from kestrel.studies.weekly_signal import (
    attention_flags,
    fear_flags,
    format_signal_study,
    news_flags,
    run_signal_study,
    stablecoin_flags,
)
from kestrel.validation.confirmation import confirm, format_confirmation
from kestrel.validation.control import PATHS, format_control, run_control
from kestrel.validation.holdout import HoldoutLog, format_holdout, holdout_test
from kestrel.validation.holdout import score as score_holdout
from kestrel.validation.log import Look, Study, ValidationLog
from kestrel.validation.robustness import check_robustness, format_robustness
from kestrel.validation.walkforward import format_walk_forward, walk_forward

app = typer.Typer(help="Kestrel trading platform.", no_args_is_help=True)

DataDir = Annotated[Path, typer.Option("--data-dir", envvar="KESTREL_DATA", help="Data root.")]
IntervalOpt = Annotated[Interval, typer.Option("--interval", "-i")]
ResearchDir = Annotated[
    Path, typer.Option("--research-dir", envvar="KESTREL_RESEARCH", help="Research notes root.")
]


@app.command()
def ingest(
    symbol: str,
    start: Annotated[str, typer.Option(help="First month, YYYY-MM.")],
    end: Annotated[
        str | None, typer.Option(help="Last month, YYYY-MM. Default: last complete.")
    ] = None,
    interval: IntervalOpt = Interval.H1,
    data_dir: DataDir = Path("data"),
) -> None:
    """Download, verify and store monthly klines from data.binance.vision."""
    first = Month.parse(start)
    last = Month.parse(end) if end else last_complete_month(datetime.now(UTC).date())
    store = ParquetStore(data_dir)

    with BinanceVisionClient() as client:
        result = client.fetch_range(symbol, interval, first, last)

    if not result.fetched:
        typer.echo(
            f"Binance publishes no {symbol.upper()} {interval.value} data for {first}..{last}. "
            "Check the pair code and that it was listed in that period.",
            err=True,
        )
        raise typer.Exit(code=1)
    store.write(symbol, interval, pl.concat(result.frames))
    typer.echo(f"fetched {len(result.fetched)} month(s) of {symbol.upper()} {interval.value}")
    if result.not_found:
        typer.echo(f"not published by Binance: {', '.join(map(str, result.not_found))}")


@app.command("ingest-universe")
def ingest_universe_cmd(
    start: Annotated[str, typer.Option(help="First month, YYYY-MM.")],
    end: Annotated[
        str | None, typer.Option(help="Last month, YYYY-MM. Default: last complete.")
    ] = None,
    interval: IntervalOpt = Interval.D1,
    workers: Annotated[int, typer.Option(min=1, max=32, help="Coins fetched at once.")] = 8,
    data_dir: DataDir = Path("data"),
) -> None:
    """Download every coin Binance's archive has ever listed against USDT, delisted ones
    included, so cross-sectional tests are free of survivorship bias. Months already
    stored are skipped, so it can be re-run to top up."""
    first = Month.parse(start)
    last = Month.parse(end) if end else last_complete_month(datetime.now(UTC).date())
    store = ParquetStore(data_dir)

    def report(coin: CoinIngest, i: int, total: int) -> None:
        if coin.fetched or i % 50 == 0 or i == total:
            typer.echo(f"[{i}/{total}] {coin.symbol}: {coin.fetched} new of {coin.archived} months")

    with ArchiveListing() as listing, BinanceVisionClient() as client:
        results, excluded = ingest_universe(
            listing, client, store, interval, first, last, workers=workers, on_coin=report
        )
    with_data = [r for r in results if r.archived]
    typer.echo(
        f"{len(with_data)} coins with {interval.value} data in {first}..{last} "
        f"({sum(r.fetched for r in results)} months fetched now); {len(excluded)} pairs "
        f"excluded. Manifest: {manifest_path(store, interval)}"
    )


@app.command("ingest-sentiment")
def ingest_sentiment(data_dir: DataDir = Path("data")) -> None:
    """Download the full Crypto Fear & Greed Index history (alternative.me). Gaps in the
    published series are reported, never filled."""
    try:
        history = sentiment.fetch()
    except (httpx.HTTPError, ValueError) as e:
        typer.echo(f"couldn't fetch the Fear & Greed history: {e}", err=True)
        raise typer.Exit(code=1) from None
    stored = sentiment.save(history, data_dir)
    f = history.frame
    first_day, last_day = f["date"][0], f["date"][-1]  # oldest first
    typer.echo(f"{f.height} days of Fear & Greed, {first_day} to {last_day}: {stored}")
    for first, last in history.gaps:
        days = (last - first).days + 1
        typer.echo(f"  gap: {first} to {last} ({days} day{'s' if days > 1 else ''} not published)")


@app.command("ingest-funding")
def ingest_funding(
    symbols: list[str],
    start: Annotated[str, typer.Option(help="First month, YYYY-MM.")] = "2020-01",
    end: Annotated[
        str | None, typer.Option(help="Last month, YYYY-MM. Default: last complete.")
    ] = None,
    data_dir: DataDir = Path("data"),
) -> None:
    """Download perpetual futures funding rates (Binance's archive, checksums verified)
    for each symbol. Read as information only; Kestrel never trades perpetuals. Gaps
    between settlements are reported, never filled."""
    first = Month.parse(start)
    last = Month.parse(end) if end else last_complete_month(datetime.now(UTC).date())
    failed = False
    for symbol in symbols:
        try:
            history = funding.fetch(symbol, first, last)
        except (httpx.HTTPError, ValueError) as e:
            typer.echo(f"couldn't fetch {symbol.upper()} funding: {e}", err=True)
            failed = True
            continue
        stored = funding.save(history, data_dir)
        f = history.frame
        typer.echo(
            f"{f.height} {history.symbol} settlements, {f['time'][0]:%Y-%m-%d} to "
            f"{f['time'][-1]:%Y-%m-%d}: {stored}"
        )
        for before, after in history.gaps:
            typer.echo(
                f"  gap: no settlement between {before:%Y-%m-%d %H:%M} and {after:%H:%M %Y-%m-%d}"
            )
        if history.not_found:
            typer.echo(f"  not published by Binance: {', '.join(map(str, history.not_found))}")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def check(
    symbol: str, interval: IntervalOpt = Interval.H1, data_dir: DataDir = Path("data")
) -> None:
    """Run data-quality checks on stored klines. Exits non-zero on integrity errors."""
    df = ParquetStore(data_dir).read(symbol, interval)
    report = check_klines(df, interval)
    typer.echo(report.summary())
    if not report.is_clean:
        raise typer.Exit(code=1)


@app.command("list")
def list_months(
    symbol: str, interval: IntervalOpt = Interval.H1, data_dir: DataDir = Path("data")
) -> None:
    """List stored months for a symbol."""
    months = ParquetStore(data_dir).months(symbol, interval)
    typer.echo("\n".join(months) if months else "(no data)")


@app.command()
def backtest(
    symbol: str,
    start: Annotated[str, typer.Option(help="First month, YYYY-MM.")],
    end: Annotated[str, typer.Option(help="Last month, YYYY-MM (inclusive).")],
    strategy: Annotated[
        str, typer.Option(help=f"One of: {', '.join(STRATEGIES)}.")
    ] = "buy-and-hold",
    interval: IntervalOpt = Interval.H4,
    cash: Annotated[float, typer.Option(help="Starting cash in USDT.")] = 330.0,
    allow_holdout: Annotated[
        bool, typer.Option(help="Allow the reserved holdout period. For one final test only.")
    ] = False,
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
    save: Annotated[bool, typer.Option(help="Save the run so it appears on the dashboard.")] = True,
    param: Annotated[
        list[str] | None, typer.Option(help="Strategy parameter as name=value; repeatable.")
    ] = None,
    research: Annotated[
        str | None,
        typer.Option(help="Research strategy slug: runs its frozen note's code as a trial."),
    ] = None,
    research_dir: ResearchDir = Path("research"),
    engine: Annotated[
        str,
        typer.Option(help="event (reference, exact) or vectorised (fast, weight strategies)."),
    ] = "event",
    explore: Annotated[
        bool,
        typer.Option(
            help="Exploration experiment: any strategy, no note, an exploration-half coin "
            "only; saved under data/exploration, never a trial and never evidence."
        ),
    ] = False,
    confirmation: Annotated[
        bool,
        typer.Option(
            help="Exploration experiment: run a frozen confirmation note (slug starting "
            "'confirmation-') on a confirmation-half coin; counted in its own trial pool."
        ),
    ] = False,
) -> None:
    """Backtest a strategy on stored bars and print its metrics."""
    if engine not in ("event", "vectorised"):
        raise typer.BadParameter("engine must be event or vectorised")
    if explore and (confirmation or research):
        raise typer.BadParameter(
            "--explore runs without a note, and never on the confirmation half"
        )
    notes = ResearchStore(research_dir)
    try:
        cfg = load_config(config)
        if explore or confirmation:
            side = cfg.split.side(symbol) if cfg.split else None
            if cfg.split is None:
                raise ValueError("no exploration split yet; run kestrel exploration-split")
            if explore and side != "exploration":
                raise ValueError(f"{symbol.upper()} isn't in the exploration half")
            if confirmation:
                if not research or not research.startswith(CONFIRMATION_PREFIX):
                    raise ValueError(
                        f"--confirmation needs a frozen note whose slug starts with "
                        f"{CONFIRMATION_PREFIX!r}"
                    )
                if side != "confirmation":
                    raise ValueError(f"{symbol.upper()} isn't in the confirmation half")
                cfg = cfg.unlock_confirmation()
        if explore:
            spec_for(strategy)  # any registered strategy, research ones included
            key, link = strategy, None
        else:
            key, link = run_target(notes, strategy, research)
        params = resolve_params(key, _parse_params(param or []))
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    except ValueError as e:
        raise typer.BadParameter(str(e)) from None
    request = RunRequest(
        strategy=key,
        symbol=symbol,
        interval=interval,
        start=Month.parse(start),
        end=Month.parse(end),
        initial_cash=Decimal(str(cash)),
        allow_holdout=allow_holdout,
        params=params,
        research=link,
        engine="vectorised" if engine == "vectorised" else "event",
        confirmation=confirmation,
    )
    try:
        outcome = execute(request, ParquetStore(data_dir), cfg)
    except (HoldoutError, ConfigError, ValueError, ConfirmationLockedError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        format_report(outcome.result, outcome.metrics, outcome.analysis, outcome.benchmark.asset)
    )
    if save and explore:
        saved = RunStore(Path(data_dir) / "exploration").save(outcome, cfg)
        typer.echo(f"\nSaved apart as exploration run {saved}: not a trial, not evidence")
    elif save:
        typer.echo(f"\nSaved as run {RunStore(data_dir).save(outcome, cfg)}")
        if link:
            notes.mark_backtested(*link)
            typer.echo(f"Logged as a trial of {link[0]} v{link[1]}")


@app.command("exploration-split")
def exploration_split(
    data_dir: DataDir = Path("data"),
    research_dir: ResearchDir = Path("research"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Split the coins for the exploration experiment, once (CLAUDE.md). Reads only each
    coin's months of daily bars and its median daily volume before the holdout, never its
    returns; refuses if a split already exists."""
    target = Path(research_dir) / "exploration" / "split.toml"
    try:
        cfg = load_config(config)
        store = ParquetStore(data_dir)
        path = manifest_path(store, Interval.D1)
        if not path.exists():
            raise ValueError("no daily coin universe stored; run kestrel ingest-universe")
        holdout = cfg.backtest.holdout_start
        volumes: dict[str, float] = {}
        for symbol in sorted(json.loads(path.read_text())["coins"]):
            if symbol in SEARCHED:
                continue
            months = [m for m in store.months(symbol, Interval.D1) if m < f"{holdout:%Y-%m}"]
            if len(months) < MIN_MONTHS:
                continue
            bars = store.read(symbol, Interval.D1, None, holdout)
            volumes[symbol] = float(bars["quote_volume"].median() or 0.0)  # type: ignore[arg-type]
        exploration, confirmation = make_split(volumes)
        write_split(
            target,
            Split(datetime.now(UTC).date(), SEED, frozenset(exploration), frozenset(confirmation)),
        )
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"{len(volumes)} eligible coins: {len(exploration)} to exploration, "
        f"{len(confirmation)} to confirmation -> {target} (commit it; it is never rewritten)"
    )


@app.command("confirm")
def confirm_cmd(
    research: Annotated[
        str, typer.Option(help="The frozen confirmation note (slug starting 'confirmation-').")
    ],
    cash: Annotated[float, typer.Option(help="Starting cash in USDT.")] = 330.0,
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """The exploration experiment's confirmation run: one graduate on every coin in the
    confirmation half, daily bars, before the holdout. Once per note; one look in the
    confirmation trial pool."""
    notes = ResearchStore(research_dir)
    log = ValidationLog(data_dir)
    try:
        if not research.startswith(CONFIRMATION_PREFIX):
            raise ValueError(f"a confirmation note's slug starts with {CONFIRMATION_PREFIX!r}")
        s, note = notes.require_testable(research)
        key = note.implementation
        assert key is not None  # freezing requires one
        if spec_for(key).universe:
            raise ValueError(f"{key} ranks the whole universe; a graduate trades one coin")
        if any(
            st["kind"] == "confirmation" and st.get("detail", {}).get("note") == research
            for st in log.studies()
        ):
            raise ValueError(f"{research} has already had its confirmation run; never rerun")
        cfg = load_config(config)
        if cfg.split is None:
            raise ValueError("no exploration split; run kestrel exploration-split")
        params = resolve_params(key, {})
        result = confirm(
            key,
            params,
            sorted(cfg.split.confirmation),
            ParquetStore(data_dir),
            cfg.unlock_confirmation(),
            Decimal(str(cash)),
        )
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    holdout = cfg.backtest.holdout_start
    study_id = log.save(
        Study(
            kind="confirmation",
            strategy=key,
            symbol="CONFIRMATION",
            interval=Interval.D1.value,
            start="first bar",
            end=f"{holdout:%Y-%m} (exclusive)",
            looks=[Look(params, result.median_sharpe)],
            verdict=result.verdict,
            detail={**result.detail(), "note": research, "version": note.version},
            confirmation=True,
        )
    )
    notes.mark_backtested(s.slug, note.version)
    typer.echo(format_confirmation(result, research))
    typer.echo(f"Saved as study {study_id}")


@app.command("anchor-study")
def anchor_study(
    symbol: str,
    start: Annotated[str, typer.Option(help="First month, YYYY-MM.")],
    end: Annotated[str, typer.Option(help="Last month, YYYY-MM (inclusive).")],
    interval: IntervalOpt = Interval.H1,
    tolerance_bps: Annotated[
        int, typer.Option(help="How close to the midpoint counts as a pullback, in bps.")
    ] = 50,
    horizon_days: Annotated[int, typer.Option(help="Days measured after each anchor day.")] = 4,
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Is Monday's range special? Every weekday's range measured the same way (backlog S1).

    A raw-effect study, run before any trading rule. It never uses the holdout."""
    try:
        cfg = load_config(config)
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    first, after = Month.parse(start), Month.parse(end).next()
    until = datetime(after.year, after.month, 1, tzinfo=UTC)
    if until > cfg.backtest.holdout_start:
        typer.echo(
            f"the range reaches the holdout (from {cfg.backtest.holdout_start:%Y-%m-%d}); "
            "studies never use it",
            err=True,
        )
        raise typer.Exit(code=1)
    bars = ParquetStore(data_dir).read(
        symbol, interval, datetime(first.year, first.month, 1, tzinfo=UTC), until
    )
    # The hurdle is one round trip of fee and spread: what a trade must clear to pay.
    c = cfg.costs
    hurdle = float(2 * (c.taker_fee_bps + c.half_spread_bps)) / 1e4
    try:
        result = run_study(
            bars,
            symbol.upper(),
            horizon_days=horizon_days,
            tolerance=tolerance_bps / 1e4,
            hurdle=hurdle,
        )
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(format_study(result))


@app.command()
def regimes(
    symbol: str,
    start: Annotated[str, typer.Option(help="First month, YYYY-MM.")] = "2017-08",
    end: Annotated[str, typer.Option(help="Last month, YYYY-MM (inclusive).")] = "2025-10",
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Market regimes from a hidden Markov model refitted monthly on past data only.

    Describes the states; it doesn't trade them. Daily bars; never uses the holdout."""
    try:
        cfg = load_config(config)
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    first, after = Month.parse(start), Month.parse(end).next()
    until = datetime(after.year, after.month, 1, tzinfo=UTC)
    if until > cfg.backtest.holdout_start:
        typer.echo(
            f"the range reaches the holdout (from {cfg.backtest.holdout_start:%Y-%m-%d}); "
            "studies never use it",
            err=True,
        )
        raise typer.Exit(code=1)
    bars = ParquetStore(data_dir).read(
        symbol, Interval.D1, datetime(first.year, first.month, 1, tzinfo=UTC), until
    )
    typer.echo(f"Fitting {symbol.upper()} month by month; this takes a minute or two...")
    try:
        result = walk_regimes(bars)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(format_regimes(result, bars, symbol.upper()))


@app.command()
def update(
    symbols: list[str],
    interval: IntervalOpt = Interval.D1,
    data_dir: DataDir = Path("data"),
) -> None:
    """Add the latest closed bars from Binance's live API to stored markets (after the
    monthly archive). Each update is audited; problems are reported, never repaired."""
    store = ParquetStore(data_dir)
    failed = False
    with httpx.Client(timeout=30) as http:
        for symbol in symbols:
            try:
                up = live.update(store, symbol, interval, http)
            except (ValueError, httpx.HTTPError) as e:
                typer.echo(f"{symbol.upper()}: {e}", err=True)
                failed = True
                continue
            last = f"{up.last:%Y-%m-%d %H:%M}" if up.last else "none"
            gaps = f", {up.quality.missing_bars} missing" if up.quality.missing_bars else ""
            state = "clean" if up.quality.is_clean else "PROBLEMS"
            typer.echo(
                f"{up.symbol} {interval.value}: {up.new_bars} new bars, last {last} UTC "
                f"({state}{gaps})"
            )
            failed = failed or not up.quality.is_clean
    if failed:
        raise typer.Exit(code=1)


@app.command("volatile-study")
def volatile_study(
    symbols: list[str],
    horizon_days: Annotated[int, typer.Option(help="Days after the signal to measure.")] = 7,
    signal: Annotated[
        str, typer.Option(help="macd (its direction) or stablecoins (supply growth).")
    ] = "macd",
    research: Annotated[
        str | None,
        typer.Option(help="The frozen note that pre-registers this run (marked as run)."),
    ] = None,
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Which way do volatile spells go? MACD's direction on the regime model's volatile days
    against the next week's return (a raw-effect study). Daily bars; never uses the holdout."""
    store = ResearchStore(research_dir)
    try:
        if research is not None:
            s, note = store.require_testable(research)
            wanted = "study:volatile-direction" if signal == "macd" else f"study:volatile-{signal}"
            if note.implementation != wanted:
                raise ValueError(f"{research} names {note.implementation}, not this study")
        cfg = load_config(config)
        markets = {}
        for symbol in symbols:
            bars = ParquetStore(data_dir).read(
                symbol, Interval.D1, None, cfg.backtest.holdout_start
            )
            if bars.is_empty():
                raise ValueError(f"no stored {symbol.upper()} 1d bars; ingest them first")
            markets[symbol.upper()] = attach_features(
                bars,
                ("regime_volatile", "stablecoins_up")
                if signal == "stablecoins"
                else ("regime_volatile",),
                symbol,
                Interval.D1,
                data_dir,
            )
        c = cfg.costs
        hurdle = math.log1p(float(2 * (c.taker_fee_bps + c.half_spread_bps)) / 1e4)
        result = run_volatile_study(
            markets, horizon_days=horizon_days, hurdle=hurdle, signal=signal
        )
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    if research is not None:
        store.mark_backtested(s.slug, note.version)
    typer.echo(format_volatile_study(result))


@app.command("ingest-attention")
def ingest_attention(symbols: list[str], data_dir: DataDir = Path("data")) -> None:
    """Daily Wikipedia pageviews of each market's article (attention), from 2015-07."""
    failed = False
    with httpx.Client(timeout=60) as http:
        for symbol in symbols:
            try:
                saved = attention.ingest(symbol, http, data_dir)
            except (ValueError, httpx.HTTPError) as e:
                typer.echo(f"{symbol.upper()}: {e}", err=True)
                failed = True
                continue
            frame = attention.load(data_dir, symbol)
            missing = attention.gaps(frame)
            last = str(frame["date"].max())
            typer.echo(
                f"{symbol.upper()}: {frame.height} days to {last} -> {saved}"
                + (f"; {len(missing)} days missing (reported, not filled)" if missing else "")
            )
    if failed:
        raise typer.Exit(code=1)


@app.command("ingest-news")
def ingest_news(symbols: list[str], data_dir: DataDir = Path("data")) -> None:
    """Daily news tone for each market from GDELT, from 2017. Slow on purpose: GDELT allows
    about one request every few seconds, so a full history takes a while. Each month is
    saved as it arrives; run it again to continue an interrupted fetch."""
    failed = False
    with httpx.Client(timeout=120) as http:
        for symbol in symbols:
            try:
                frame = news.ingest(
                    symbol, http, data_dir, on_month=lambda m: typer.echo(f"  {m:%Y-%m}")
                )
            except (ValueError, httpx.HTTPError) as e:
                typer.echo(f"{symbol.upper()}: {e}", err=True)
                failed = True
                continue
            typer.echo(f"{symbol.upper()}: {frame.height} days of news tone")
    if failed:
        raise typer.Exit(code=1)


@app.command("ingest-stablecoins")
def ingest_stablecoins(data_dir: DataDir = Path("data")) -> None:
    """Download the daily USD-pegged stablecoin supply from DefiLlama (from 2017-11)."""
    try:
        with httpx.Client(timeout=60) as http:
            frame = stablecoins.fetch(http)
    except (httpx.HTTPError, stablecoins.StablecoinError) as e:
        typer.echo(f"stablecoins: {e}", err=True)
        raise typer.Exit(code=1) from None
    stablecoins.save(frame, data_dir)
    missing = stablecoins.gaps(frame)
    first, last = frame["date"].to_list()[0], frame["date"].to_list()[-1]
    typer.echo(
        f"{frame.height} days of stablecoin supply, {first} to {last}"
        + (f"; {len(missing)} missing days (not filled)" if missing else "")
    )


@app.command("ingest-delistings")
def ingest_delistings(data_dir: DataDir = Path("data")) -> None:
    """Binance's spot delisting announcements (from 2022-02): when each coin's delisting was
    announced and when its trading stopped. Announcements only, no prices. Articles that
    can't be read are reported, never guessed."""
    try:
        with httpx.Client(timeout=60) as http:
            frame, problems = delistings.ingest(http, data_dir)
    except (httpx.HTTPError, delistings.DelistingError) as e:
        typer.echo(f"delistings: {e}", err=True)
        raise typer.Exit(code=1) from None
    announcements = frame["article"].n_unique()
    amended = frame.filter(pl.col("amended_by").is_not_null()).height
    first, last = frame["announced_at"].min(), frame["announced_at"].max()
    assert isinstance(first, datetime)
    assert isinstance(last, datetime)
    typer.echo(
        f"{frame.height} coins in {announcements} announcements, {first:%Y-%m-%d} to "
        f"{last:%Y-%m-%d}" + (f"; {amended} removal dates amended" if amended else "")
    )
    for problem in problems:
        typer.echo(f"  problem: {problem}")


@app.command("signal-study")
def signal_study(
    signal: Annotated[
        str, typer.Argument(help="attention, extreme-fear, news-tone or stablecoins.")
    ],
    symbols: list[str],
    research: Annotated[
        str | None,
        typer.Option(help="The frozen note that pre-registers this run (marked as run)."),
    ] = None,
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Do the weeks after a signal beat the weeks without it? A raw-effect study on Sunday
    closes, daily bars, never the holdout."""
    store = ResearchStore(research_dir)
    try:
        if research is not None:
            s, note = store.require_testable(research)
            if note.implementation != f"study:{signal}":
                raise ValueError(f"{research} names {note.implementation}, not study:{signal}")
        cfg = load_config(config)
        markets: dict[str, tuple[pl.DataFrame, list[bool | None]]] = {}
        for symbol in symbols:
            bars = ParquetStore(data_dir).read(
                symbol, Interval.D1, None, cfg.backtest.holdout_start
            )
            if bars.is_empty():
                raise ValueError(f"no stored {symbol.upper()} 1d bars; ingest them first")
            if signal == "attention":
                flags = attention_flags(bars, attention.load(data_dir, symbol))
            elif signal == "news-tone":
                flags = news_flags(bars, news.load(data_dir, symbol))
            elif signal == "extreme-fear":
                bars = attach_features(bars, ("fear_greed",), symbol, Interval.D1, data_dir)
                flags = fear_flags(bars)
            elif signal == "stablecoins":
                flags = stablecoin_flags(bars, stablecoins.load(data_dir))
            else:
                raise ValueError(
                    f"unknown signal {signal!r}; one of attention, extreme-fear, news-tone, "
                    "stablecoins"
                )
            markets[symbol.upper()] = (bars, flags)
        c = cfg.costs
        hurdle = math.log1p(float(2 * (c.taker_fee_bps + c.half_spread_bps)) / 1e4)
        result = run_signal_study(signal, markets, hurdle=hurdle)
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    if research is not None:
        store.mark_backtested(s.slug, note.version)
    typer.echo(format_signal_study(result))


@app.command("delisting-study")
def delisting_study(
    research: Annotated[
        str,
        typer.Option(help="The frozen note that pre-registers this run (marked as run)."),
    ],
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """What happens between a Binance delisting announcement and removal? Every coin
    removed before the holdout, daily bars, run once through its frozen note."""
    store = ResearchStore(research_dir)
    try:
        s, note = store.require_testable(research)
        if note.implementation != "study:delistings":
            raise ValueError(f"{research} names {note.implementation}, not study:delistings")
        paths, problems = measure_all(data_dir, load_config(config))
        result = run_delisting_study(paths, problems)
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    store.mark_backtested(s.slug, note.version)
    typer.echo(format_delisting_study(result))


@app.command("ingest-delisting-perps")
def ingest_delisting_perps(
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """USDT-M perp daily bars and funding for each delisted coin the spot study measured,
    only for the months its window spans (for the hypothetical delisting short study)."""
    try:
        paths, _ = measure_all(data_dir, load_config(config))
        with httpx.Client(timeout=60, follow_redirects=True) as http:
            found = ingest_perps(
                paths, str(data_dir), http, on_coin=lambda c, s: typer.echo(f"  {c}: {s}")
            )
    except (ConfigError, ValueError, httpx.HTTPError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    have = sum(s is not None for s in found.values())
    typer.echo(f"{have} of {len(found)} coins have perp bars for their window")


@app.command("delisting-short-study")
def delisting_short_study(
    research: Annotated[
        str,
        typer.Option(help="The frozen note that pre-registers this run (marked as run)."),
    ],
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Hypothetical: shorting each delisted coin's USDT-M perp from day 1 to removal, at 2x
    with liquidations. Run once through its frozen note; never the holdout."""
    store = ResearchStore(research_dir)
    try:
        s, note = store.require_testable(research)
        if note.implementation != "study:delisting-short":
            raise ValueError(f"{research} names {note.implementation}, not study:delisting-short")
        cfg = load_config(config)
        spot_fee = float(cfg.costs.taker_fee_bps) / 1e4
        spot_store = ParquetStore(data_dir)
        paths: list[ShortPath] = []
        problems: list[str] = []
        for spot in measure_all(data_dir, cfg)[0]:
            measured: ShortPath | str = f"{spot.coin}: no perp bars stored for its window"
            for symbol in perp_symbols(spot.coin):
                bars, rates = load_perp(str(data_dir), spot, symbol)
                if bars.is_empty():
                    continue
                day1 = datetime.combine(spot.announced, datetime.min.time(), tzinfo=UTC)
                spot_day1 = spot_store.read(
                    f"{spot.coin}USDT",
                    Interval.D1,
                    day1 + timedelta(days=1),
                    day1 + timedelta(days=2),
                )
                open1 = float(spot_day1["open"][0]) if spot_day1.height else None
                measured = short_path(spot, symbol, bars, rates, open1, spot_fee=spot_fee)
                break
            if isinstance(measured, str):
                problems.append(measured)
            else:
                paths.append(measured)
        result = run_short_study(paths, problems)
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    store.mark_backtested(s.slug, note.version)
    typer.echo(format_short_study(result))


@app.command("holdout-test")
def holdout_test_cmd(
    symbol: str,
    strategy: Annotated[str, typer.Option(help="The implementation the note names.")],
    research: Annotated[str, typer.Option(help="The frozen research note that names this test.")],
    warmup_start: Annotated[
        str, typer.Option(help="First month the strategy runs (before the holdout), YYYY-MM.")
    ],
    start: Annotated[str, typer.Option(help="First scored month, YYYY-MM.")],
    end: Annotated[str, typer.Option(help="Last scored month, YYYY-MM (inclusive).")],
    interval: IntervalOpt = Interval.D1,
    param: Annotated[
        list[str] | None,
        typer.Option(help="A setting the note names, as name=value; repeatable."),
    ] = None,
    cash: Annotated[float, typer.Option(help="Starting cash in USDT.")] = 330.0,
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """The one look at the holdout, for a finished strategy's frozen note. Each note may
    use it once; every run is recorded in <data>/holdout/. Never a trial."""
    store = ResearchStore(research_dir)
    log = HoldoutLog(data_dir)
    try:
        key, link = run_target(store, strategy, research)
        if link is None:  # pragma: no cover - run_target always links a research slug
            raise ValueError("the holdout is only for a frozen research note")
        if key != strategy:
            raise ValueError(f"{research} names {key}, not {strategy}")
        if log.used(link[0]):
            raise ValueError(f"{link[0]} has already used the holdout; it is used once")
        cfg = load_config(config)
        first, scored, last = Month.parse(warmup_start), Month.parse(start), Month.parse(end)
        after = last.next()
        begin = datetime(scored.year, scored.month, 1, tzinfo=UTC)
        until = datetime(after.year, after.month, 1, tzinfo=UTC)
        params = resolve_params(key, _parse_params(param or []))
        money = Decimal(str(cash))
        warm = datetime(first.year, first.month, 1, tzinfo=UTC)
        store_ = ParquetStore(data_dir)
        if spec_for(key).universe:
            # A basket: the portfolio engine on the universe panel, against BTC held.
            panel = strategy_panel(store_, key, interval, first, last)
            btc = store_.read(UNIVERSE_BENCHMARK, interval, warm, until)
            typer.echo(f"HOLDOUT USED: {key} on {UNIVERSE} for {link[0]} v{link[1]}")
            mine = run_portfolio(
                make_portfolio_strategy(key, params), panel, interval, cfg, money,
                allow_holdout=True,
            )  # fmt: skip
            held = run_backtest(
                BuyAndHold(UNIVERSE_BENCHMARK), btc, interval, cfg, money, allow_holdout=True
            )
            result = score_holdout(key, UNIVERSE, params, mine, held, start=begin, end=until)
        else:
            bars = store_.read(symbol, interval, warm, until)
            bars = attach_features(bars, spec_for(key).features, symbol, interval, data_dir)
            typer.echo(f"HOLDOUT USED: {key} on {symbol.upper()} for {link[0]} v{link[1]}")
            result = holdout_test(
                key, symbol.upper(), bars, interval, cfg, money, params, start=begin, end=until
            )
        log.record(link[0], link[1], result, datetime(first.year, first.month, 1, tzinfo=UTC))
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    store.mark_backtested(*link)
    typer.echo(format_holdout(result))
    typer.echo(f"Recorded in {log.root / (link[0] + '.json')}")


@app.command("walk-forward")
def walk_forward_cmd(
    symbol: str,
    start: Annotated[str, typer.Option(help="First month, YYYY-MM.")],
    end: Annotated[str, typer.Option(help="Last month, YYYY-MM (inclusive).")],
    strategy: Annotated[str, typer.Option(help="Strategy key.")],
    grid: Annotated[
        list[str] | None,
        typer.Option(
            help="A parameter's values as name=a,b,c; repeatable. Leave out to judge the "
            "fixed rule (its defaults) window by window."
        ),
    ] = None,
    interval: IntervalOpt = Interval.H4,
    train_months: Annotated[int, typer.Option(help="Months each choice is made on.")] = 24,
    test_months: Annotated[int, typer.Option(help="Months each choice is judged on.")] = 6,
    cash: Annotated[float, typer.Option(help="Starting cash in USDT.")] = 330.0,
    research: Annotated[
        str | None,
        typer.Option(help="Research strategy slug: its frozen note must name the strategy."),
    ] = None,
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Choose parameters on rolling training windows and judge them on the windows after
    (P3). Never uses the holdout."""
    try:
        key, _ = run_target(ResearchStore(research_dir), strategy, research)
        values = _parse_grid(grid or [])
        cfg = load_config(config)
        first, last = Month.parse(start), Month.parse(end)
        after = last.next()
        money = Decimal(str(cash))
        plug: dict[str, Any] = {}
        if spec_for(key).universe:
            # A basket: each window's panel through the portfolio engine, against BTC held.
            bars = strategy_panel(ParquetStore(data_dir), key, interval, first, last)
            btc = ParquetStore(data_dir).read(
                UNIVERSE_BENCHMARK,
                interval,
                datetime(first.year, first.month, 1, tzinfo=UTC),
                datetime(after.year, after.month, 1, tzinfo=UTC),
            )

            def hold_btc(t0: datetime, t2: datetime) -> BacktestResult:
                window = btc.filter((pl.col("open_time") >= t0) & (pl.col("open_time") < t2))
                return run_backtest(BuyAndHold(UNIVERSE_BENCHMARK), window, interval, cfg, money)

            plug = {
                "build": lambda p: make_portfolio_strategy(key, p),
                "run": lambda s, frame: run_portfolio(s, frame, interval, cfg, money),
                "hold": hold_btc,
            }
            symbol = UNIVERSE
        else:
            bars = ParquetStore(data_dir).read(
                symbol,
                interval,
                datetime(first.year, first.month, 1, tzinfo=UTC),
                datetime(after.year, after.month, 1, tzinfo=UTC),
            )
            bars = attach_features(bars, spec_for(key).features, symbol, interval, data_dir)
        result = walk_forward(
            key,
            symbol.upper(),
            bars,
            interval,
            cfg,
            money,
            values,
            start=first,
            end=last,
            train_months=train_months,
            test_months=test_months,
            **plug,
        )
    except (HoldoutError, ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(format_walk_forward(result))
    study = Study(
        kind="walk-forward",
        strategy=key,
        symbol=symbol.upper(),
        interval=interval.value,
        start=start,
        end=end,
        looks=[Look(params, sharpe) for params, sharpe in result.looks],
        verdict="SURVIVES" if result.survives else "KILL",
        detail={
            "is_sharpe": result.is_sharpe,
            "oos_sharpe": result.oos_sharpe,
            "oos_return": result.oos_return,
            "benchmark_return": result.benchmark_return,
            "benchmark_sharpe": result.benchmark_sharpe,
            "folds": len(result.folds),
            "windows_beating_holding": result.windows_beating_holding,
        },
    )
    typer.echo(f"Saved as study {ValidationLog(data_dir).save(study)}")


@app.command()
def robustness(
    symbol: str,
    start: Annotated[str, typer.Option(help="First month, YYYY-MM.")],
    end: Annotated[str, typer.Option(help="Last month, YYYY-MM (inclusive).")],
    strategy: Annotated[str, typer.Option(help="Strategy key.")],
    interval: IntervalOpt = Interval.H4,
    param: Annotated[
        list[str] | None,
        typer.Option(help="Starting setting as name=value; repeatable. Default: the defaults."),
    ] = None,
    cash: Annotated[float, typer.Option(help="Starting cash in USDT.")] = 330.0,
    research: Annotated[
        str | None,
        typer.Option(help="Research strategy slug: its frozen note must name the strategy."),
    ] = None,
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Move each parameter around its setting and check the result survives: a plateau
    or a lucky peak? (P3). Every setting is saved as a trial. Never uses the holdout."""
    store = ParquetStore(data_dir)
    money = Decimal(str(cash))
    try:
        key, _ = run_target(ResearchStore(research_dir), strategy, research)
        spec = spec_for(key)
        center = resolve_params(key, _parse_params(param or []))
        cfg = load_config(config)
        first, last = Month.parse(start), Month.parse(end)
        evaluate: Callable[[dict[str, int]], Metrics]
        if spec.universe:
            panel = strategy_panel(store, key, interval, first, last)

            def evaluate(p: dict[str, int]) -> Metrics:
                strategy_ = make_portfolio_strategy(key, p)
                return compute_metrics(run_portfolio(strategy_, panel, interval, cfg, money))
        else:
            after = last.next()
            bars = store.read(
                symbol,
                interval,
                datetime(first.year, first.month, 1, tzinfo=UTC),
                datetime(after.year, after.month, 1, tzinfo=UTC),
            )
            if bars.height < 2:
                raise ValueError(f"no stored {symbol.upper()} {interval.value} bars in that range")
            bars = attach_features(bars, spec.features, symbol, interval, data_dir)

            def evaluate(p: dict[str, int]) -> Metrics:
                strategy_ = make_strategy(key, symbol, p)
                return compute_metrics(run_backtest(strategy_, bars, interval, cfg, money))

        result = check_robustness(key, spec.params, center, evaluate)
    except (HoldoutError, ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    market = f"{symbol.upper()} {interval.value}, {start} to {end}"
    typer.echo(format_robustness(result, market))
    settings = [result.center, *result.neighbours]
    study = Study(
        kind="robustness",
        strategy=key,
        symbol=symbol.upper(),
        interval=interval.value,
        start=start,
        end=end,
        looks=[Look(s.params, s.sharpe) for s in settings if s.error is None],
        verdict=result.verdict,
        detail={
            "center": result.center.params,
            "positive_share": result.positive_share,
            "median_sharpe": result.median_sharpe,
            "center_sharpe": result.center.sharpe,
        },
    )
    typer.echo(f"Saved as study {ValidationLog(data_dir).save(study)}")


@app.command("random-entry")
def random_entry(
    symbol: str,
    start: Annotated[str, typer.Option(help="First month, YYYY-MM.")],
    end: Annotated[str, typer.Option(help="Last month, YYYY-MM (inclusive).")],
    strategy: Annotated[str, typer.Option(help="Strategy key (a bracket strategy).")],
    interval: IntervalOpt = Interval.H4,
    param: Annotated[
        list[str] | None,
        typer.Option(help="Setting as name=value; repeatable. Default: the defaults."),
    ] = None,
    paths: Annotated[int, typer.Option(help="Random-entry copies to run.")] = PATHS,
    cash: Annotated[float, typer.Option(help="Starting cash in USDT.")] = 330.0,
    research: Annotated[
        str | None,
        typer.Option(help="Research strategy slug: its frozen note must name the strategy."),
    ] = None,
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Is a bracket strategy better than random entries with the same stops, targets and
    sizing? Scorecard test 9. Saved as a study; never uses the holdout."""
    try:
        key, _ = run_target(ResearchStore(research_dir), strategy, research)
        spec = spec_for(key)
        settings = resolve_params(key, _parse_params(param or []))
        if spec.universe:
            raise ValueError(f"{key} ranks a universe; the control is for one-coin brackets")
        made = make_strategy(key, symbol, settings)
        if not isinstance(made, BracketStrategy):
            raise ValueError(f"{key} holds weights, not brackets: there is no payoff shape to copy")
        cfg = load_config(config)
        first, last = Month.parse(start), Month.parse(end)
        after = last.next()
        bars = ParquetStore(data_dir).read(
            symbol,
            interval,
            datetime(first.year, first.month, 1, tzinfo=UTC),
            datetime(after.year, after.month, 1, tzinfo=UTC),
        )
        if bars.height < 2:
            raise ValueError(f"no stored {symbol.upper()} {interval.value} bars in that range")
        bars = attach_features(bars, spec.features, symbol, interval, data_dir)
        tick = max(1, paths // 10)
        result = run_control(
            made,
            bars,
            interval,
            cfg,
            Decimal(str(cash)),
            paths=paths,
            on_path=lambda i: (
                typer.echo(f"  {i}/{paths} paths", err=True) if i % tick == 0 else None
            ),
        )
    except (HoldoutError, ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(format_control(result, f"{symbol.upper()} {interval.value}, {start} to {end}"))
    study = Study(
        kind="random-entry",
        strategy=key,
        symbol=symbol.upper(),
        interval=interval.value,
        start=start,
        end=end,
        looks=[],  # random paths aren't settings chosen from the data: no trials
        verdict=result.verdict,
        detail={
            "params": settings,
            "paths": paths,
            "expectancy_r": result.expectancy_r,
            "trades": result.trades,
            "beaten_share": result.beaten_share,
            "random_median_r": result.quantile(0.5),
            "random_p95_r": result.quantile(0.95),
        },
    )
    typer.echo(f"Saved as study {ValidationLog(data_dir).save(study)}")


@app.command("cost-gate")
def cost_gate_cmd(
    symbols: list[str],
    interval: IntervalOpt = Interval.M1,
    swing_bars: Annotated[int, typer.Option(help="Bars either side of a swing high.")] = 3,
    setup: Annotated[
        str, typer.Option(help="fvg (S2/S3 fair value gaps) or order-flow (batch 2 C).")
    ] = "fvg",
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Can a 1m/5m setup's stops carry a round trip's costs? Measures stop widths only
    (no outcomes, not a trial), on all stored bars before the holdout."""
    try:
        cfg = load_config(config)
        c = cfg.costs
        round_trip = float(2 * (c.taker_fee_bps + c.half_spread_bps))
        gates: list[cost_gate.Gate] = []
        spans: list[tuple[datetime, datetime]] = []
        for symbol in symbols:
            bars = ParquetStore(data_dir).read(symbol, interval, None, cfg.backtest.holdout_start)
            if bars.is_empty():
                raise ValueError(
                    f"no stored {symbol.upper()} {interval.value} bars; ingest them first"
                )
            if setup == "fvg":
                found = cost_gate.setups(bars, swing_bars=swing_bars)
            elif setup == "order-flow":
                found = cost_gate.order_flow_setups(bars)
            else:
                raise ValueError(f"unknown setup {setup!r}; fvg or order-flow")
            first, last = bars["open_time"][0], bars["open_time"][-1]
            spans.append((first, last))
            market = ("order flow " if setup == "order-flow" else "") + (
                f"{symbol.upper()} {interval.value}"
            )
            typer.echo(f"{market}: {bars.height} bars, {first:%Y-%m-%d} to {last:%Y-%m-%d}")
            gates += [
                cost_gate.measure(market, found, round_trip),
                cost_gate.measure(market, found, round_trip, ny_only=True),
            ]
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo("")
    typer.echo(cost_gate.format_gates(gates, setup))
    first = min(a for a, _ in spans)
    last = max(b for _, b in spans)
    saved = cost_gate.save(gates, interval.value, first, last, data_dir, setup)
    typer.echo(f"Saved for the dashboard: {saved}")


def _parse_grid(pairs: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        parts = [v.strip() for v in raw.split(",")]
        if not sep or not all(v.lstrip("-").isdigit() for v in parts):
            raise ValueError(f"--grid must be name=<whole numbers, comma separated>, got {pair!r}")
        out[name.strip()] = [int(v) for v in parts]
    return out


def _parse_params(pairs: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not value.strip().lstrip("-").isdigit():
            raise ValueError(f"--param must be name=<whole number>, got {pair!r}")
        out[name.strip()] = int(value)
    return out


LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
"""Where macOS launchd looks for per-user jobs."""

paper_app = typer.Typer(help="Paper trading on the simulated venue (P4).", no_args_is_help=True)
app.add_typer(paper_app, name="paper")


@paper_app.command("open")
def paper_open(
    name: str,
    symbol: Annotated[str, typer.Option(help="Market to trade.")],
    strategy: Annotated[str, typer.Option(help=f"One of: {', '.join(STRATEGIES)}.")],
    research: Annotated[
        str | None, typer.Option(help="Research note a research strategy runs under.")
    ] = None,
    interval: IntervalOpt = Interval.D1,
    cash: Annotated[float, typer.Option(help="Starting cash in USDT.")] = 330.0,
    venue: Annotated[
        str, typer.Option(help="sim (simulated fills) or testnet (Binance Spot Testnet orders).")
    ] = "sim",
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
) -> None:
    """Open a paper account. Its first decision is on the last bar closed now; the strategy
    runs at the settings its note (or its defaults) fix, and the terms never change."""
    try:
        if venue not in ("sim", "testnet"):
            raise ValueError(f"--venue must be sim or testnet, not {venue!r}")
        key, link = run_target(ResearchStore(research_dir), strategy, research)
        now = datetime.now(UTC)
        step = interval.duration
        # The bar that closed most recently: its close is the first decision.
        first = datetime.fromtimestamp(
            (now.timestamp() // step.total_seconds() - 1) * step.total_seconds(), UTC
        )
        terms = paper_trading.Terms(
            name, key, symbol.upper(), interval, resolve_params(key, {}), Decimal(str(cash)), first,
            link[0] if link else None, venue,  # type: ignore[arg-type]
        )  # fmt: skip
        paper_trading.PaperAccount.open(data_dir, terms)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"Opened paper account {name}: {key} on {terms.symbol} {interval.value} ({venue}), "
        f"{cash:g} USDT, first decision on the {first:%Y-%m-%d %H:%M} UTC bar"
    )


@paper_app.command("run")
def paper_run(
    name: str,
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Bring the market up to date, then handle every closed bar the account hasn't:
    mark, decide, and trade at the next open. Refuses to trade on data with problems."""
    account = paper_trading.PaperAccount(data_dir, name)
    try:
        terms = account.terms()
        cfg = load_config(config)
        store = ParquetStore(data_dir)
        with httpx.Client(timeout=30) as http:
            features = spec_for(terms.strategy).features
            # The traded market, and any market a feature is read from (BTC for btc_close).
            for market in dict.fromkeys([terms.symbol.upper(), *markets_for(features)]):
                up = live.update(store, market, terms.interval, http)
                if not up.quality.is_clean:
                    raise ValueError(
                        f"{market} data has problems; not trading:\n{up.quality.summary()}"
                    )
            bars = store.read(terms.symbol, terms.interval)
            bars = attach_features(bars, features, terms.symbol, terms.interval, data_dir)
            venue = _paper_venue(terms, cfg, http)
            _reconcile(account, venue, cfg)
            handled = paper_trading.run(
                account, bars, cfg,
                lambda at: live.open_of(terms.symbol, terms.interval, at, http),
                venue=venue, now=datetime.now(UTC),
            )  # fmt: skip
            _reconcile(account, venue, cfg)
    except (ValueError, ConfigError, httpx.HTTPError, BinanceError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    halt_reason = safety.halted(account)
    if not handled and halt_reason is None:
        typer.echo(f"{name}: nothing new (the next bar hasn't closed)")
    for h in handled:
        outcome = (
            "MISSED (the run was late; a live venue can't fill at a past open)" if h.missed
            else "within the band, no trade" if h.order is None
            else f"{h.order.side.value} {h.order.quantity} at {h.order.price:.2f}"
            if isinstance(h.order, paper_trading.Fill)
            else f"REJECTED: {h.order.reason}"
        )  # fmt: skip
        typer.echo(
            f"{h.bar:%Y-%m-%d}: equity {h.equity:.2f}, target {float(h.target):.0%}: {outcome}"
        )
    if halt_reason is not None:
        typer.echo(f"HALTED: {halt_reason}", err=True)
        raise typer.Exit(code=1)


def _paper_venue(
    terms: paper_trading.Terms, cfg: Config, http: httpx.Client
) -> BinanceVenue | None:
    """The account's venue: None for the simulated one (the runner makes it)."""
    if terms.venue != "testnet":
        return None
    client = BinanceClient(Credentials.from_env(), http, TESTNET_URL)
    return BinanceVenue(
        client, terms.symbol, cfg.account_currency, CostModel(cfg.costs), terms.initial_cash
    )


def _reconcile(
    account: paper_trading.PaperAccount, venue: BinanceVenue | None, cfg: Config
) -> None:
    """An exchange account must hold at least what the ledger says; if not, halt."""
    if venue is None or safety.halted(account) is not None:
        return
    state = account.state()
    holdings = [(venue.base, state.position), (venue.quote, state.cash)]
    short = safety.reconcile(holdings, venue.balances())
    if short:
        safety.halt(account, f"reconciliation failed: {safety.describe(short)}")


@paper_app.command("track")
def paper_track(
    name: str,
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Compare a paper account with its replay on the simulated venue: decisions (which must
    match), fill costs against the cost model, and equity."""
    account = paper_trading.PaperAccount(data_dir, name)
    try:
        terms = account.terms()
        bars = ParquetStore(data_dir).read(terms.symbol, terms.interval)
        bars = attach_features(
            bars, spec_for(terms.strategy).features, terms.symbol, terms.interval, data_dir
        )
        result = tracking.track(account, bars, load_config(config))
    except (ValueError, ConfigError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(tracking.format_tracking(name, result))
    if result.decision_mismatches:
        raise typer.Exit(code=1)


@paper_app.command("schedule")
def paper_schedule(
    name: str,
    remove: Annotated[bool, typer.Option(help="Delete the job instead.")] = False,
    agents_dir: Annotated[
        Path, typer.Option(help="Where launchd looks for per-user jobs.")
    ] = LAUNCH_AGENTS,
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Write (or remove) a macOS launchd job that runs the account every hour at 10 past.
    Hourly is safe (a run with nothing new does nothing) and catches up after sleep; bars
    close at 00:00 UTC, which launchd's local-time clock can't name across daylight saving.
    It is written, not loaded: the command to load it is printed."""
    account = paper_trading.PaperAccount(data_dir, name)
    label = f"com.kestrel.paper.{name}"
    path = agents_dir / f"{label}.plist"
    if remove:
        path.unlink(missing_ok=True)
        typer.echo(f"Removed {path}. If it was loaded: launchctl bootout gui/$(id -u)/{label}")
        return
    try:
        account.terms()
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    kestrel_bin = Path(sys.executable).parent / "kestrel"
    data, cfg = data_dir.resolve(), config.resolve()
    command = (
        "source ~/.zshrc >/dev/null 2>&1; "  # the testnet keys, never written into the job
        f"exec {shlex.quote(str(kestrel_bin))} paper run {shlex.quote(name)} "
        f"--data-dir {shlex.quote(str(data))} --config {shlex.quote(str(cfg))}"
    )
    log = account.dir.resolve() / "schedule.log"
    job = {
        "Label": label,
        "ProgramArguments": ["/bin/zsh", "-c", command],
        "StartCalendarInterval": [{"Minute": 10}],
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    agents_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(job))
    typer.echo(f"Wrote {path}: runs {name} hourly at 10 past, logging to {log}")
    typer.echo(f"Load it with:  launchctl bootstrap gui/$(id -u) {shlex.quote(str(path))}")


@paper_app.command("resume")
def paper_resume(
    name: str,
    reason: Annotated[str, typer.Option(help="Why it is safe to trade again (recorded).")],
    data_dir: DataDir = Path("data"),
) -> None:
    """Let a halted account trade again. The reason goes in its ledger."""
    account = paper_trading.PaperAccount(data_dir, name)
    try:
        account.terms()
        was = safety.halted(account)
        safety.resume(account, reason)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{name} resumed (was halted: {was})")


@app.command()
def kill(
    name: str,
    reason: Annotated[str, typer.Option(help="Why (recorded in the ledger).")],
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """The kill switch: sell a paper account's whole position at market now, then halt it."""
    account = paper_trading.PaperAccount(data_dir, name)
    try:
        terms = account.terms()
        cfg = load_config(config)
        stored = ParquetStore(data_dir).read(terms.symbol, terms.interval)
        volume = Decimal(str(stored["quote_volume"][-1])) if stored.height else Decimal(0)
        with httpx.Client(timeout=30) as http:
            venue = _paper_venue(terms, cfg, http) or SimVenue(
                cfg, terms.initial_cash, rules=cfg.rules_for
            )
            price = live.last_price(terms.symbol, http)
            event = safety.kill(account, venue, price, volume, reason)
    except (ValueError, ConfigError, httpx.HTTPError, BinanceError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    sold = (
        f"sold {event['quantity']} at {Decimal(event['fill_price']):.2f}" if "quantity" in event
        else f"SALE REJECTED: {event['rejected']}" if "rejected" in event
        else "nothing held"
    )  # fmt: skip
    typer.echo(f"KILLED {name}: {sold}; halted until resumed")


@paper_app.command("status")
def paper_status(name: str, data_dir: DataDir = Path("data")) -> None:
    """Where a paper account stands."""
    account = paper_trading.PaperAccount(data_dir, name)
    try:
        terms, state = account.terms(), account.state()
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    events = account.ledger.events()
    marks = [e for e in events if e["type"] == "mark"]
    # A kill's sale is a fill too: it is recorded as its own event.
    fills = sum(e["type"] == "fill" or (e["type"] == "kill" and "side" in e) for e in events)
    last = (
        f"{float(marks[-1]['equity']):.2f} USDT on {marks[-1]['bar'][:10]}"
        if marks
        else "not yet marked"
    )
    typer.echo(f"{name}: {terms.strategy} on {terms.symbol} {terms.interval.value}"
               + (f" (note {terms.research})" if terms.research else ""))  # fmt: skip
    typer.echo(f"  started with {terms.initial_cash} USDT; equity {last}")
    reason = safety.halted(account)
    if reason:
        typer.echo(f"  HALTED: {reason}")
    typer.echo(f"  holding {state.position} {terms.symbol}, cash {state.cash:.2f}; {fills} fills; "
               f"{len(marks)} days handled")  # fmt: skip


@app.command()
def serve(
    data_dir: DataDir = Path("data"),
    host: Annotated[
        str, typer.Option(help="Bind address. Keep local unless you know why.")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes (dev).")] = False,
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
    research_dir: ResearchDir = Path("research"),
) -> None:
    """Run the API and dashboard at http://HOST:PORT."""
    import os

    import uvicorn

    os.environ["KESTREL_DATA"] = str(data_dir)
    os.environ["KESTREL_CONFIG"] = str(config)
    os.environ["KESTREL_RESEARCH"] = str(research_dir)
    os.environ["KESTREL_HOST"] = host
    typer.echo(f"Kestrel dashboard: http://{host}:{port}   API docs: http://{host}:{port}/api/docs")
    uvicorn.run("kestrel.api.server:app", host=host, port=port, reload=reload, factory=False)


if __name__ == "__main__":
    app()
