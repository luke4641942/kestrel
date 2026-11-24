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
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
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
    visible,
)
from kestrel.config import Config, ConfigError, load_config
from kestrel.data import (
    attention,
    delistings,
    external,
    funding,
    fx,
    live,
    news,
    rates,
    sentiment,
    stablecoins,
)
from kestrel.data.binance_vision import BinanceVisionClient, Month, last_complete_month
from kestrel.data.features import attach_features, markets_for
from kestrel.data.quality import check_klines
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.data.universe import (
    TOKENIZED,
    ArchiveListing,
    CoinIngest,
    ProductTags,
    ingest_universe,
    manifest_path,
)
from kestrel.execution import paper as paper_trading
from kestrel.execution import safety, tracking
from kestrel.execution import status as status_export
from kestrel.execution.alerts import Send as AlertSend
from kestrel.execution.alerts import mac_notifier
from kestrel.execution.alerts import review as alert_review
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
from kestrel.regimes import FAST_PRESET, RegimeStore, format_regimes
from kestrel.regimes import PRESET as REGIME_PRESET
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
from kestrel.studies.cross_section import (
    MIN_VOLUME,
    build_panel,
    format_cross_study,
    run_cross_study,
)
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
    SIGNALS,
    above_normal,
    attention_flags,
    coinbase_premium,
    fear_flags,
    format_signal_study,
    liquidity_flags,
    net_liquidity,
    news_flags,
    run_signal_study,
    stablecoin_flags,
    variance_premium,
)
from kestrel.studies.weekly_signal import save as save_signal_study
from kestrel.validation import decay
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

    with ArchiveListing() as listing, BinanceVisionClient() as client, ProductTags() as tags:
        products = tags.products()
        results, excluded, unclassified = ingest_universe(
            listing, client, store, interval, first, last,
            products=products, workers=workers, on_coin=report,
        )  # fmt: skip
    with_data = [r for r in results if r.archived]
    typer.echo(
        f"{len(with_data)} coins with {interval.value} data in {first}..{last} "
        f"({sum(r.fetched for r in results)} months fetched now); {len(excluded)} pairs "
        f"excluded ({sum(e.reason == TOKENIZED for e in excluded)} tokenized stocks). "
        f"Manifest: {manifest_path(store, interval)}"
    )
    if unclassified:
        typer.echo(
            f"{len(unclassified)} delisted pair(s) ending in BUSDT kept as coins; Binance's "
            f"product list no longer says whether they were tokenized stocks: "
            f"{', '.join(unclassified)}"
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
    """Split the coins for the exploration experiment, once (docs/rules.md). Reads only each
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


@app.command("overlap")
def overlap_report(
    runs: Annotated[
        list[str], typer.Argument(help="Saved runs as name=run_id, e.g. tvt-btc=20261115-...")
    ],
    data_dir: DataDir = Path("data"),
) -> None:
    """How much saved strategies overlap: correlation (all days and BTC's bad days), BTC
    beta, returns by BTC's month, and what each adds to an equal mix. Descriptive: reads
    saved runs, so it counts no trials."""
    from kestrel.validation.overlap import daily_returns, overlap

    try:
        pairs = [r.split("=", 1) for r in runs]
        if any(len(p) != 2 for p in pairs):
            raise ValueError("each run is name=run_id")
        saved = RunStore(data_dir)
        for _, run_id in pairs:
            if saved.load(run_id).meta.get("used_holdout"):
                raise ValueError(f"{run_id} used the holdout; overlap reads pre-holdout runs")
        strategies = {n: daily_returns(saved.load(i).equity, n) for n, i in pairs}
        bars = ParquetStore(data_dir).read("BTCUSDT", Interval.D1, None, None).sort("open_time")
        btc = bars.select(
            (pl.col("open_time") + pl.duration(days=1)).alias("close_time"),
            pl.col("close").pct_change().alias("BTC"),
        ).drop_nulls()
        o = overlap(strategies, btc)
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"{o.days} common days, {o.first} to {o.last}")
    with pl.Config(
        tbl_width_chars=200, float_precision=2, tbl_rows=30, tbl_hide_dataframe_shape=True
    ):
        typer.echo("\nDaily-return correlation, all days:")
        typer.echo(str(o.correlation))
        typer.echo(f"\nOn BTC's bad days (down more than 3%; {o.bad_days} days):")
        typer.echo(str(o.bad_correlation))
        typer.echo("\nMean daily return (%) by BTC's month (falling < -5%, rising > +5%):")
        typer.echo(str(o.by_btc_month))
    typer.echo("\nBTC beta and share of daily variance BTC explains:")
    for n, x in o.exposure.items():
        typer.echo(f"  {n:<12} beta {x.beta:.2f}  R2 {x.r2:.0%}")
    typer.echo("\nSharpe over the common days (in-sample; selected strategies, so optimistic):")
    for n, s in o.alone.items():
        typer.echo(f"  {n:<12} {s:.2f}")
    typer.echo(f"  equal mix    {o.mix_sharpe:.2f}")
    for n, s in o.without.items():
        typer.echo(f"  mix without {n:<12} {s:.2f}")


def _commit() -> str | None:
    """This code's git commit, marked -dirty with uncommitted changes."""
    from kestrel.provenance import code_version

    v = code_version()
    return v.commit and v.commit + ("-dirty" if v.dirty else "")


@app.command("event-study")
def event_study(
    event: Annotated[str, typer.Argument(help="perp-listing, token-unlocks or coinbase-listing.")],
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """An event study (kestrel.studies.events), run once from its frozen note: what coins
    do after an event, against the other coins at the same time, before the holdout.
    Exploration-half coins plus BTC, ETH and SOL; never the locked confirmation half."""
    from kestrel.data.panel import load_panel
    from kestrel.studies import events
    from kestrel.studies.cross_section import perp_funding

    notes = {
        "perp-listing": ("does-a-perp-listing-mark-a-top", "study:perp-listing"),
        "token-unlocks": ("do-coins-fall-into-large-token-unlocks", "study:token-unlocks"),
        "coinbase-listing": (
            "does-a-coinbase-listing-move-a-coin-on-binance",
            "study:coinbase-listing",
        ),
    }
    if event not in notes:
        typer.echo(f"no event study {event!r}; one of {', '.join(notes)}", err=True)
        raise typer.Exit(code=1)
    slug, implementation = notes[event]
    store = ResearchStore(research_dir)
    try:
        s, note = store.require_testable(slug)
        if note.implementation != implementation:
            raise ValueError(f"{slug} names {note.implementation}, not {implementation}")
        cfg = load_config(config)
        if cfg.split is None:
            raise ValueError("no exploration split; the study's universe is its exploration half")
        holdout = cfg.backtest.holdout_start
        last = holdout - timedelta(days=1)
        coins = sorted(cfg.split.exploration | {"BTCUSDT", "ETHUSDT", "SOLUSDT"})
        panel: pl.DataFrame = load_panel(
            ParquetStore(data_dir), coins, Interval.D1, Month(2017, 8), Month(last.year, last.month)
        )
        panel = panel.filter(pl.col("open_time") < holdout)
        tiers = [(float(v), float(b) / 1e4) for v, b in cfg.universe.spread_tiers]
        if event == "token-unlocks":
            from kestrel.data import unlocks

            schedules, gecko = unlocks.load(data_dir)
            mapping, dropped = unlocks.ticker_map(schedules, gecko)
            mapped = {p: c for p, c in mapping.items() if c in coins}
            target = Path(data_dir) / "unlocks" / "mapping.json"
            target.write_text(json.dumps({"mapped": mapping, "dropped": dropped}, indent=2))
            result, splits, unlocked, path = events.run_token_unlocks(
                panel, schedules, mapped, holdout, tiers
            )
            store.mark_backtested(s.slug, note.version)
            report = events.format_unlocks(result, splits, len({u.coin for u in unlocked}))
            typer.echo(report)
            saved = events.save(event, slug, result, report, path, data_dir, _commit())
            typer.echo(f"Saved for the dashboard: {saved}")
            typer.echo(f"\nMapping written to {target} ({len(mapping)} protocols mapped).")
            return
        if event == "coinbase-listing":
            # Its own universe: every coin outside the locked half (note v1).
            stored = [s for s, iv in ParquetStore(data_dir).datasets() if iv is Interval.D1]
            coins = events.open_universe(stored, cfg.split.confirmation)
            panel = load_panel(
                ParquetStore(data_dir), coins, Interval.D1, Month(2017, 8),
                Month(last.year, last.month),
            ).filter(pl.col("open_time") < holdout)  # fmt: skip
            listed = external.load(data_dir, external.LISTINGS)
            days = {f"{b}USDT": d for b, d in zip(listed["base"], listed["first_day"], strict=True)}
            result, found, path = events.run_coinbase_listing(
                panel, {c: d for c, d in days.items() if c in coins}, holdout, tiers
            )
            store.mark_backtested(s.slug, note.version)
            report = events.format_result(f"Coinbase listings (note {slug})", result)
            typer.echo(report)
            typer.echo(f"\n{len(found)} events found (the note counted them before any return).")
            saved = events.save(event, slug, result, report, path, data_dir, _commit())
            typer.echo(f"Saved for the dashboard: {saved}")
            return
        first: dict[str, datetime | None] = {}
        for c in coins:
            f = perp_funding(str(data_dir), c)
            first[c] = None if f is None or f.height == 0 else f["time"].min()  # type: ignore[assignment]
        result, found, path = events.run_perp_listing(panel, first, holdout, tiers)
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    store.mark_backtested(s.slug, note.version)
    report = events.format_result(f"Perp listings (note {slug})", result)
    typer.echo(report)
    typer.echo(f"\n{len(found)} events found (the note counted 102 before any return was read).")
    saved = events.save(event, slug, result, report, path, data_dir, _commit())
    typer.echo(f"Saved for the dashboard: {saved}")


@app.command("short-study")
def short_study(
    name: Annotated[
        str,
        typer.Argument(
            help="btc-regime-trend, btc-rotation, trend-vol-target, long-short-momentum, "
            "cash-and-carry or perp-listing."
        ),
    ],
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """A hypothetical shorting study (docs/rules.md, "Parked"), run once from its
    frozen note, before the holdout. Each market is logged as a trial; a pass is
    needs-shorting, never traded."""
    from kestrel.data.panel import load_panel
    from kestrel.studies import shorting
    from kestrel.studies.cross_section import perp_funding
    from kestrel.validation.deflated import deflated_sharpe, trial_set
    from kestrel.validation.log import Look, Study, ValidationLog

    if name not in shorting.NOTES:
        typer.echo(f"no shorting study {name!r}; one of {', '.join(shorting.NOTES)}", err=True)
        raise typer.Exit(code=1)
    slug, implementation = shorting.NOTES[name]
    store = ResearchStore(research_dir)
    try:
        s, note = store.require_testable(slug)
        if note.implementation != implementation:
            raise ValueError(f"{slug} names {note.implementation}, not {implementation}")
        cfg = load_config(config)
        holdout = cfg.backtest.holdout_start
        last = holdout - timedelta(days=1)
        bars = ParquetStore(data_dir)

        def panel_for(coins: list[str]) -> pl.DataFrame:
            panel: pl.DataFrame = load_panel(
                bars, coins, Interval.D1, Month(2017, 8), Month(last.year, last.month)
            )
            return panel.filter(pl.col("open_time") < holdout)

        def funding_for(coin: str) -> pl.DataFrame | None:
            f = perp_funding(str(data_dir), coin)
            return None if f is None else f.filter(pl.col("time") < holdout)

        tiers = [(float(v), float(b) / 1e4) for v, b in cfg.universe.spread_tiers]
        exploration = sorted(cfg.split.exploration) if cfg.split else None
        outcome = shorting.run_note(
            name, panel_for, funding_for, tiers, float(cfg.costs.taker_fee_bps) / 1e4,
            exploration, holdout,
        )  # fmt: skip
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    log = ValidationLog(data_dir)
    first = outcome.results[0]
    span = (str(getattr(first, "first", "")), str(getattr(first, "last", "")))
    for market, sharpe in outcome.looks:
        log.save(
            Study(
                kind="short-study", strategy=implementation, symbol=market, interval="1d",
                start=span[0], end=span[1], looks=[Look({}, sharpe)], verdict=outcome.verdict,
                detail={"note": slug, "hypothetical": True},
            )
        )  # fmt: skip
    trials = trial_set(RunStore(data_dir).summaries(), log.studies())
    for r in outcome.results:
        if isinstance(r, shorting.LegResult):
            r.deflated = deflated_sharpe(r.sharpe, r.days, r.skew, r.kurtosis, trials)
        elif isinstance(r, shorting.NeutralResult):
            r.deflated = deflated_sharpe(r.sharpe, r.days, None, None, trials)
        elif isinstance(r, shorting.ListingShortResult):
            r.deflated = deflated_sharpe(r.sharpe, r.days, r.skew, r.kurtosis, trials)
    text = outcome.text
    if any(isinstance(r, shorting.LegResult) for r in outcome.results):
        legs = [r for r in outcome.results if isinstance(r, shorting.LegResult)]
        need = 1 if name == "btc-rotation" else 2
        text = shorting.format_leg(text.splitlines()[0], legs, need)
    elif isinstance(first, shorting.NeutralResult):
        text = shorting.format_neutral(first)
    elif isinstance(first, shorting.ListingShortResult):
        text = shorting.format_listing_short(first)
    store.mark_backtested(s.slug, note.version)
    outcome.text = text
    from kestrel.provenance import code_version

    version = code_version()
    commit = (
        None if version.commit is None else version.commit + ("-dirty" if version.dirty else "")
    )
    saved = shorting.save(outcome, slug, data_dir, commit)
    typer.echo(text)
    typer.echo(f"\nLogged {len(outcome.looks)} trial(s); the main pool now has {trials.count}.")
    typer.echo(f"Saved for the dashboard: {saved}")


@app.command("cross-study")
def cross_study(
    signal: Annotated[str, typer.Argument(help="funding or taker-flow.")],
    research: Annotated[
        str, typer.Option(help="The frozen note that pre-registers this run (marked as run).")
    ],
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Does a coin's funding or taker flow predict its next week against the other coins,
    beyond its past week and size? Exploration-half coins plus BTC, ETH and SOL (never the
    locked confirmation half), Sunday closes, before the holdout."""
    implementations = {"funding": "study:funding-cross-section", "taker-flow": "study:taker-flow"}
    store = ResearchStore(research_dir)
    try:
        if signal not in implementations:
            raise ValueError("signal must be funding or taker-flow")
        s, note = store.require_testable(research)
        if note.implementation != implementations[signal]:
            raise ValueError(
                f"{research} names {note.implementation}, not {implementations[signal]}"
            )
        cfg = load_config(config)
        if cfg.split is None:
            raise ValueError("no exploration split; the study's universe is its exploration half")
        coins = sorted(cfg.split.exploration | SEARCHED)
        panel = build_panel(str(data_dir), coins, cfg.backtest.holdout_start)
        # One weekly round trip at the narrowest band an eligible (20M+) coin can't beat:
        # the taker fee and the 20M-100M band's half-spread, both ways.
        c = cfg.costs
        band = min(float(b) for v, b in cfg.universe.spread_tiers if float(v) == MIN_VOLUME)
        hurdle = math.log1p(2 * (float(c.taker_fee_bps) + band) / 1e4)
        result = run_cross_study(panel, signal, hurdle)  # type: ignore[arg-type]
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    store.mark_backtested(s.slug, note.version)
    typer.echo(format_cross_study(result))


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
    interval: Annotated[
        str, typer.Option(help="1d (the preset) or 4h (findings 23's 4h model).")
    ] = "1d",
    save: Annotated[
        bool, typer.Option(help="Store the fit (data/regimes/, or data/regimes/4h/).")
    ] = False,
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Market regimes from a hidden Markov model refitted monthly on past data only.

    Describes the states; it doesn't trade them. Never uses the holdout. A 4h fit is what
    the regime_volatile_4h feature reads; the dashboard shows only daily fits."""
    if interval not in ("1d", "4h"):
        typer.echo("--interval is 1d or 4h", err=True)
        raise typer.Exit(code=1)
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
        symbol, Interval(interval), datetime(first.year, first.month, 1, tzinfo=UTC), until
    )
    preset = REGIME_PRESET if interval == "1d" else FAST_PRESET
    typer.echo(f"Fitting {symbol.upper()} {interval} month by month; this takes a few minutes...")
    try:
        result = walk_regimes(bars, preset)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(format_regimes(result, bars, symbol.upper()))
    if save:
        RegimeStore(data_dir, interval).save(symbol.upper(), result, bars)
        typer.echo(f"Saved to {RegimeStore(data_dir, interval).root / symbol.upper()}.json")


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


NEWS_GAP = 60.0
"""Seconds between markets in a gentle (scheduled) news run."""


@app.command("ingest-news")
def ingest_news(
    symbols: list[str],
    max_months: Annotated[
        int | None, typer.Option(min=1, help="At most this many months per market per run.")
    ] = None,
    data_dir: DataDir = Path("data"),
) -> None:
    """Daily news tone for each market from GDELT, from 2017. Slow on purpose: GDELT allows
    about one request every few seconds, so a full history takes a while. Each month is
    saved as it arrives; run it again to continue an interrupted fetch. With --max-months
    (an hourly schedule) it is gentle: the market furthest behind goes first, each gets one
    try per month asked for, plus one retry 30 seconds after a refusal, a minute apart, and
    a refusal or a dropped connection waits for the next run instead of failing it. Within a
    run, months are 10 seconds apart: half GDELT's stated limit at most."""
    failed = False
    gentle = max_months is not None
    order = news.furthest_behind(symbols, data_dir) if gentle else symbols
    with httpx.Client(timeout=120) as http:
        for k, symbol in enumerate(order):
            if gentle and k:
                time.sleep(NEWS_GAP)
            try:
                frame = news.ingest(
                    symbol, http, data_dir, limit=max_months,
                    on_month=lambda m: typer.echo(f"  {m:%Y-%m}"),
                    backoff=news.GENTLE_BACKOFF if gentle else news.BACKOFF,
                )  # fmt: skip
            except (news.NewsRefusedError, httpx.TransportError) as e:
                if not gentle:
                    typer.echo(f"{symbol.upper()}: {e}", err=True)
                    failed = True
                    continue
                reason = (
                    "refused (rate limit)" if isinstance(e, news.NewsRefusedError) else "dropped"
                )
                typer.echo(f"{symbol.upper()}: GDELT {reason}; the next run tries again")
                continue
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


@app.command("ingest-unlocks")
def ingest_unlocks(data_dir: DataDir = Path("data")) -> None:
    """Token unlock schedules from DefiLlama's public dataset files, and CoinGecko's coin
    list (ids to tickers). Schedules only, no prices. Files that can't be read are
    reported, never guessed. Takes a few minutes (one file per protocol)."""
    from kestrel.data import unlocks

    def progress(k: int, n: int) -> None:
        if k % 50 == 0 or k == n:
            typer.echo(f"  {k}/{n} protocols")

    try:
        with httpx.Client(timeout=60, follow_redirects=True) as http:
            schedules, coins, problems = unlocks.ingest(http, data_dir, progress=progress)
    except httpx.HTTPError as e:
        typer.echo(f"unlocks: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"{schedules['protocol'].n_unique()} protocols with a schedule, "
        f"{coins.height} CoinGecko coins; {len(problems)} problems"
    )
    for problem in problems[:20]:
        typer.echo(f"  problem: {problem}")


@app.command("signal-study")
def signal_study(
    signal: Annotated[str, typer.Argument(help=f"One of: {', '.join(SIGNALS)}.")],
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
        unadjusted: dict[str, tuple[pl.DataFrame, list[bool | None]]] = {}
        for symbol in symbols:
            bars = ParquetStore(data_dir).read(
                symbol, Interval.D1, None, cfg.backtest.holdout_start
            )
            if bars.is_empty():
                raise ValueError(f"no stored {symbol.upper()} 1d bars; ingest them first")
            if signal == "attention":
                flags = attention_flags(bars, attention.load(data_dir, symbol))
            elif signal == "news-tone":
                tone = news.load(data_dir, symbol)
                last = (cfg.backtest.holdout_start - timedelta(days=1)).date()
                news.require_through(tone, symbol, last)
                flags = news_flags(bars, tone)
            elif signal == "extreme-fear":
                bars = attach_features(bars, ("fear_greed",), symbol, Interval.D1, data_dir)
                flags = fear_flags(bars)
            elif signal == "stablecoins":
                flags = stablecoin_flags(bars, stablecoins.load(data_dir))
            elif signal == "coinbase-premium":
                coin = f"coinbase:{_coin(symbol)}-USD"
                cb, usdt = (
                    external.load(data_dir, coin),
                    external.load(data_dir, "coinbase:USDT-USD"),
                )
                times = bars["open_time"].to_list()
                flags = above_normal(times, coinbase_premium(bars, cb, usdt))
                # Beside it, deciding nothing (the note): from 2018-01 with USDT as a dollar.
                plain = above_normal(times, coinbase_premium(bars, cb, None))
                unadjusted[symbol.upper()] = (bars, _since(times, plain, date(2018, 1, 1)))
            elif signal == "variance-premium":
                dvol = external.load(data_dir, f"dvol:{_coin(symbol)}")
                flags = above_normal(bars["open_time"].to_list(), variance_premium(bars, dvol))
            elif signal == "net-liquidity":
                weekly = net_liquidity(
                    external.load(data_dir, "fred:WALCL"),
                    external.load(data_dir, "fred:WTREGEN"),
                    external.load(data_dir, "fred:RRPONTSYD"),
                )
                times = bars["open_time"].to_list()
                flags = _since(times, liquidity_flags(times, weekly), date(2018, 1, 1))
            else:
                raise ValueError(f"unknown signal {signal!r}; one of {', '.join(SIGNALS)}")
            markets[symbol.upper()] = (bars, flags)
        c = cfg.costs
        hurdle = math.log1p(float(2 * (c.taker_fee_bps + c.half_spread_bps)) / 1e4)
        result = run_signal_study(signal, markets, hurdle=hurdle)
        beside = run_signal_study(signal, unadjusted, hurdle=hurdle) if unadjusted else None
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    report = format_signal_study(result)
    if beside is not None:
        report += "\n\nBeside it, deciding nothing: from 2018-01, USDT taken as a dollar\n\n"
        report += format_signal_study(beside)
    if research is not None:
        store.mark_backtested(s.slug, note.version)
        save_signal_study(result, s.slug, report, data_dir, _commit(), beside)
    typer.echo(report)


def _coin(symbol: str) -> str:
    return symbol.upper().removesuffix("USDT")


def _since(times: list[datetime], flags: list[bool | None], first: date) -> list[bool | None]:
    """The flags, unknown before ``first`` (a note's span)."""
    return [f if t.date() >= first else None for t, f in zip(times, flags, strict=True)]


@app.command("ingest-external")
def ingest_external(data_dir: DataDir = Path("data")) -> None:
    """Download the outside daily series the signal studies read (kestrel.data.external):
    Coinbase closes, Deribit's DVOL, FRED's liquidity series. Gaps are reported."""
    today = datetime.now(UTC).date()
    try:
        with httpx.Client(timeout=60) as http:
            for name, every in external.SERIES.items():
                frame = external.fetch(name, http, date(2017, 1, 1), today)
                external.save(frame, data_dir, name)
                missing = external.gaps(frame, every)
                days = frame["date"].to_list() or [None]
                typer.echo(
                    f"{name}: {frame.height} rows, {days[0]} to {days[-1]}"
                    + (f"; {len(missing)} missing (not filled)" if missing else "")
                )
    except (httpx.HTTPError, external.ExternalError) as e:
        typer.echo(f"external: {e}", err=True)
        raise typer.Exit(code=1) from None


@app.command("ingest-fx")
def ingest_fx(
    pair: Annotated[
        list[str] | None, typer.Option(help="One pair (repeatable); default every [fx] pair.")
    ] = None,
    start: Annotated[str, typer.Option(help="First day, YYYY-MM-DD.")] = "2002-01-01",
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Daily forex candles (bid, ask, mid) from OANDA for the pairs fixed in [fx], with a
    quality report per pair, and each currency's interest rate (FRED) for financing.
    Read-only; the token comes from KESTREL_OANDA_TOKEN. A pair with integrity errors is
    reported and not saved."""
    try:
        cfg = load_config(config)
        if cfg.fx is None:
            raise ConfigError("no [fx] table in the config")
        wanted = [p.upper() for p in pair] if pair else list(cfg.fx.pairs)
        unknown = sorted(set(wanted) - set(cfg.fx.pairs))
        if unknown:
            raise ConfigError(f"not in [fx] pairs (fixed; adding one is a note): {unknown}")
        auth = fx.token()
        since = datetime.fromisoformat(start).replace(tzinfo=UTC)
    except (ConfigError, fx.FxError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    failed = False
    with httpx.Client(timeout=60) as http:
        for name in wanted:
            try:
                frame = fx.fetch_daily(name, http, auth, since)
            except (httpx.HTTPError, fx.FxError) as e:
                typer.echo(f"{name}: {e}", err=True)
                failed = True
                continue
            report = fx.check(frame, name, cfg.fx.holdout_start)
            typer.echo(report.summary())
            if report.is_clean:
                fx.save(frame, data_dir, name)
            else:
                failed = True
        typer.echo("Interest rates for financing (OECD 3-month interbank, monthly, FRED):")
        try:
            for r in rates.ingest(data_dir, http, date(2005, 1, 1)):
                blanks = ", ".join(f"{m:%Y-%m}" for m in r.missing) or "none"
                typer.echo(
                    f"  {r.currency}: {r.first} to {r.last}; blank months since 2005: {blanks}"
                )
        except (httpx.HTTPError, external.ExternalError) as e:
            typer.echo(f"rates: {e}", err=True)
            failed = True
    if failed:
        raise typer.Exit(code=1)


@app.command("fx-backtest")
def fx_backtest_command(
    research: Annotated[str, typer.Option(help="The frozen forex note (fx:... strategy).")],
    start: Annotated[str, typer.Option(help="First month traded, YYYY-MM.")],
    end: Annotated[str, typer.Option(help="Last month traded, YYYY-MM (inclusive).")],
    cash: Annotated[float, typer.Option(help="Starting equity, in [fx] account_currency.")] = 500,
    param: Annotated[list[str] | None, typer.Option(help="name=value (repeatable).")] = None,
    warmup_from: Annotated[
        str | None, typer.Option(help="First month the strategy may see (its note says).")
    ] = None,
    research_dir: ResearchDir = Path("research"),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Backtest a frozen forex note across the [fx] pairs, before the forex holdout. Saved
    under data/fx/runs/ and counted in the forex trial pool."""
    from kestrel.fx_backtest import fx_backtest

    try:
        run = fx_backtest(
            ResearchStore(research_dir),
            research,
            data_dir,
            load_config(config),
            start,
            end,
            cash,
            _parse_params(param or []),
            warmup_from,
        )
    except (ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(run.report)


@app.command("ingest-coinbase-listings")
def ingest_coinbase_listings(
    data_dir: DataDir = Path("data"),
    research_dir: ResearchDir = Path("research"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Each study coin's first trading day on Coinbase Exchange (dates only: no price is
    read or kept), for the Coinbase-listing study: every stored coin outside the locked
    confirmation half. Dates already saved are kept and not fetched again. Tens of
    minutes: one request per 300 days of each product's history, 0.25 s apart."""
    from kestrel.studies.events import open_universe

    try:
        cfg = load_config(config)
        if cfg.split is None:
            raise ValueError("no exploration split: the locked half is unknown")
        stored = [s for s, iv in ParquetStore(data_dir).datasets() if iv is Interval.D1]
        bases = {c.removesuffix("USDT") for c in open_universe(stored, cfg.split.confirmation)}
        try:
            known = external.load(data_dir, external.LISTINGS)
        except external.ExternalError:
            known = pl.DataFrame(schema=external.LISTING_SCHEMA)
        end = (cfg.backtest.holdout_start - timedelta(days=1)).date()
        with httpx.Client(timeout=60) as http:
            fresh = external.coinbase_listings(bases - set(known["base"].to_list()), http, end)
    except (ConfigError, ValueError, OSError, httpx.HTTPError) as e:
        typer.echo(f"coinbase listings: {e}", err=True)
        raise typer.Exit(code=1) from None
    frame = pl.concat([known, fresh]).unique("base").sort("base")
    external.save(frame, data_dir, external.LISTINGS)
    typer.echo(f"{frame.height} of {len(bases)} study coins have traded on Coinbase by {end}")


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
        if link[0].startswith(CONFIRMATION_PREFIX):
            # The exploration experiment's confirmation: only such a note may read the
            # locked confirmation half (kestrel.exploration).
            cfg = cfg.unlock_confirmation()
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
            panel = visible(strategy_panel(store_, key, interval, first, last), cfg, key)
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
    except (ConfigError, ValueError, ConfirmationLockedError) as e:
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
            bars = visible(
                strategy_panel(ParquetStore(data_dir), key, interval, first, last), cfg, key
            )
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
            panel = visible(strategy_panel(store, key, interval, first, last), cfg, key)

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
        if spec_for(key).universe and symbol.upper() != UNIVERSE:
            raise ValueError(f"{key} trades a basket: open it with --symbol {UNIVERSE}")
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
    alerts: Annotated[
        bool,
        typer.Option(
            "--alerts/--no-alerts",
            help="Afterwards, send a macOS notification for anything new: a halt, a failed "
            "run, falling behind, a missed day, a trade (kestrel.execution.alerts).",
        ),
    ] = False,
) -> None:
    """Bring the market up to date, then handle every closed bar the account hasn't:
    mark, decide, and trade at the next open. Refuses to trade on data with problems."""
    account = paper_trading.PaperAccount(data_dir, name)
    failure: str | None = None
    network = False
    try:
        _paper_run(account, name, data_dir, config)
    except _RunFailedError as e:
        failure = str(e)
        network = e.network
        typer.echo(failure, err=True)
        raise typer.Exit(code=1) from None
    except (typer.Exit, KeyboardInterrupt):
        raise
    except Exception as e:  # a bug, not a known failure: alert, then crash loudly
        failure = f"crashed: {type(e).__name__}: {e}"
        raise
    finally:
        if alerts:
            check: decay.Decay | None = None
            try:
                check = _decay_check(
                    account, _tracking(account, data_dir, config), data_dir, config
                )
            except Exception:
                check = None
            sent = alert_review(
                account, datetime.now(UTC), _notifier(), failure, check, network=network
            )
            for a in sent:
                typer.echo(f"alert sent: {a.message}")


class _RunFailedError(Exception):
    """A paper run that couldn't finish; the message says why. ``network``: the connection
    failed (no route, a name that won't resolve), not Kestrel or the data."""

    def __init__(self, message: str, network: bool = False) -> None:
        super().__init__(message)
        self.network = network


def _notifier() -> AlertSend:
    return mac_notifier()


def _paper_run(
    account: paper_trading.PaperAccount, name: str, data_dir: Path, config: Path
) -> None:
    try:
        if spec_for(account.terms().strategy).universe:
            _paper_run_basket(account, data_dir, config)
            return
    except (ValueError, ConfigError, httpx.HTTPError, ConfirmationLockedError) as e:
        raise _RunFailedError(str(e), network=isinstance(e, httpx.TransportError)) from None
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
            if stablecoins.FEATURE in features and bars.height:
                # The supply the latest closed bar may read (known from D + 2 days).
                last_bar = bars["open_time"].to_list()[-1]  # bars are sorted
                needed = (last_bar + timedelta(days=1) - stablecoins.KNOWN_AFTER).date()
                got = stablecoins.refresh(data_dir, http, needed)
                if got.revised:
                    typer.echo(
                        f"stablecoins: DefiLlama revised {got.revised} saved days (largest "
                        f"{got.largest:.2%}); the saved figures are kept",
                        err=True,
                    )
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
        raise _RunFailedError(str(e), network=isinstance(e, httpx.TransportError)) from None
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
    """The account's venue: None for the simulated one (the runner makes it). A basket's
    venue trades its strategy's fixed coins."""
    if terms.venue != "testnet":
        return None
    client = BinanceClient(Credentials.from_env(), http, TESTNET_URL)
    spec = spec_for(terms.strategy)
    symbols = list(spec.assets) if spec.universe else terms.symbol
    return BinanceVenue(
        client, symbols, cfg.account_currency, CostModel(cfg.costs), terms.initial_cash
    )


def _reconcile(
    account: paper_trading.PaperAccount, venue: BinanceVenue | None, cfg: Config
) -> None:
    """The exchange account must hold at least what every testnet account's ledger says,
    added up (they share one exchange account: two can hold BTC); if not, halt this one."""
    if venue is None or safety.halted(account) is not None:
        return
    root = account.dir.parent.parent
    owed: dict[str, Decimal] = {}
    for name in paper_trading.accounts(root):
        other = paper_trading.PaperAccount(root, name)
        terms, state = other.terms(), other.state()
        if terms.venue != "testnet":
            continue
        positions = state.positions or ({terms.symbol: state.position} if state.position else {})
        for asset, amount in venue.assets(state.cash, positions):
            owed[asset] = owed.get(asset, Decimal(0)) + amount
    short = safety.reconcile(sorted(owed.items()), venue.balances())
    if short:
        safety.halt(account, f"reconciliation failed: {safety.describe(short)}")


def _paper_run_basket(account: paper_trading.PaperAccount, data_dir: Path, config: Path) -> None:
    """``paper run`` for a basket strategy: bring every coin it can hold (and every market
    its features read) up to date, then handle each closed bar through the portfolio
    engine's day step."""
    terms = account.terms()
    cfg = load_config(config)
    store = ParquetStore(data_dir)
    spec = spec_for(terms.strategy)
    coins = list(spec.assets)
    if not coins:
        raise ValueError(f"{terms.strategy} has no fixed coin set; a paper basket needs one")
    with httpx.Client(timeout=30) as http:
        for market in dict.fromkeys([*coins, *markets_for(spec.features)]):
            up = live.update(store, market, terms.interval, http)
            if not up.quality.is_clean:
                raise ValueError(
                    f"{market} data has problems; not trading:\n{up.quality.summary()}"
                )
        panel = paper_trading.basket_panel(store, terms, coins, datetime.now(UTC))

        last_seen = dict(panel.group_by("symbol").agg(pl.col("open_time").max()).iter_rows())

        def forming(at: datetime) -> dict[str, Decimal]:
            opens = {c: live.open_of(c, terms.interval, at, http) for c in last_seen}
            return paper_trading.complete_opens(opens, last_seen, at, terms.interval.duration)

        try:
            venue = _paper_venue(terms, cfg, http)
            _reconcile(account, venue, cfg)
            handled = paper_trading.run_basket(
                account, panel, cfg, forming, venue=venue, now=datetime.now(UTC)
            )
            _reconcile(account, venue, cfg)
        except BinanceError as e:
            raise ValueError(str(e)) from None
    halt_reason = safety.halted(account)
    if not handled and halt_reason is None:
        typer.echo(f"{account.dir.name}: nothing new (the next bar hasn't closed)")
    for h in handled:
        orders = ", ".join(
            f"{o.side.value} {o.quantity} {o.symbol} at {o.price:.4f}"
            if isinstance(o, paper_trading.Fill)
            else f"REJECTED {o.symbol}: {o.reason}"
            for o in h.orders
        )
        decided = (
            "no decision"
            if h.target is None
            else "cash"
            if not h.target
            else ", ".join(f"{s} {float(w or 0):.0%}" for s, w in sorted(h.target.items()))
        )
        typer.echo(
            f"{h.bar:%Y-%m-%d}: equity {h.equity:.2f}, {decided}"
            + (f": {orders}" if orders else "")
            + (" (MISSED: the run was late)" if h.missed else "")
        )
    if halt_reason is not None:
        typer.echo(f"HALTED: {halt_reason}", err=True)
        raise typer.Exit(code=1)


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
        result = _tracking(account, data_dir, config)
        check = _decay_check(account, result, data_dir, config)
    except (ValueError, ConfigError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(tracking.format_tracking(name, result))
    typer.echo(decay.format_decay(check))
    if result.decision_mismatches:
        raise typer.Exit(code=1)


def _tracking(
    account: paper_trading.PaperAccount, data_dir: Path, config: Path
) -> tracking.Tracking:
    """An account against its replay on the simulated venue, over the stored data."""
    terms = account.terms()
    spec = spec_for(terms.strategy)
    if spec.universe:
        panel = paper_trading.basket_panel(
            ParquetStore(data_dir), terms, list(spec.assets), datetime.now(UTC)
        )
        return tracking.track_basket(account, panel, load_config(config))
    bars = ParquetStore(data_dir).read(terms.symbol, terms.interval)
    bars = attach_features(bars, spec.features, terms.symbol, terms.interval, data_dir)
    return tracking.track(account, bars, load_config(config))


def _decay_check(
    account: paper_trading.PaperAccount, result: tracking.Tracking, data_dir: Path, config: Path
) -> decay.Decay:
    """The decay check (kestrel.validation.decay); its backtest runs only once it's due."""
    terms = account.terms()
    return decay.assess(
        decay.model_returns(result),
        lambda: decay.reference_returns(terms, data_dir, load_config(config)),
    )


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
        f"--data-dir {shlex.quote(str(data))} --config {shlex.quote(str(cfg))} --alerts"
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
        store = ParquetStore(data_dir)

        def last_volume(symbol: str) -> Decimal:
            stored = store.read(symbol, terms.interval)
            return Decimal(str(stored["quote_volume"][-1])) if stored.height else Decimal(0)

        with httpx.Client(timeout=30) as http:
            venue = _paper_venue(terms, cfg, http) or SimVenue(
                cfg, terms.initial_cash, rules=cfg.rules_for
            )
            if spec_for(terms.strategy).universe:
                held = [s for s, q in account.state().positions.items() if q > 0]
                event = safety.kill_basket(
                    account,
                    venue,
                    {s: live.last_price(s, http) for s in held},
                    {s: last_volume(s) for s in held},
                    reason,
                )
                sales = [
                    f"sold {x['quantity']} {x['symbol']} at {Decimal(x['fill_price']):.4f}"
                    if "quantity" in x
                    else f"SALE OF {x['symbol']} REJECTED: {x['rejected']}"
                    for x in event["sales"]
                ]
                typer.echo(f"KILLED {name}: {'; '.join(sales) or 'nothing held'}; halted")
                return
            price = live.last_price(terms.symbol, http)
            event = safety.kill(account, venue, price, last_volume(terms.symbol), reason)
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
    fills += sum("side" in x for e in events if e["type"] == "kill" for x in e.get("sales", []))
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
    held = (
        ", ".join(f"{q} {s}" for s, q in state.positions.items()) or "nothing"
        if spec_for(terms.strategy).universe
        else f"{state.position} {terms.symbol}"
    )
    typer.echo(f"  holding {held}, cash {state.cash:.2f}; {fills} fills; "
               f"{len(marks)} days handled")  # fmt: skip


@paper_app.command("export")
def paper_export(
    push: Annotated[
        bool, typer.Option(help="Commit it, with TODO.md, and push to the private remote.")
    ] = False,
    repo: Annotated[Path, typer.Option(help="The git repository to publish from.")] = Path("."),
    remote: Annotated[
        str, typer.Option(help="The git remote of the private status repository.")
    ] = status_export.REMOTE,
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Every paper account as one JSON snapshot (equity, profit, holdings, trades, the
    model check, what each is waiting for). Printed, or with --push committed with the
    repository's TODO.md (gitignored) under refs/kestrel/status, never a branch here, and
    pushed to the main branch of a private repository (remote 'status')."""
    try:
        text = status_export.render(status_export.snapshot(data_dir, load_config(config)))
    except (ValueError, ConfigError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    if not push:
        typer.echo(text, nl=False)
        return
    try:
        files = {status_export.FILE: text}
        todo = repo / "TODO.md"
        if todo.exists():
            files["TODO.md"] = todo.read_text()
        said = status_export.publish(status_export.git_in(repo), files, remote)
    except status_export.PublishError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC: {said}")


@paper_app.command("schedule-export")
def paper_schedule_export(
    remove: Annotated[bool, typer.Option(help="Delete the job instead.")] = False,
    agents_dir: Annotated[
        Path, typer.Option(help="Where launchd looks for per-user jobs.")
    ] = LAUNCH_AGENTS,
    repo: Annotated[Path, typer.Option(help="The git repository to publish from.")] = Path("."),
    data_dir: DataDir = Path("data"),
    config: Annotated[
        Path, typer.Option("--config", envvar="KESTREL_CONFIG", help="Config file.")
    ] = Path("kestrel.toml"),
) -> None:
    """Write (or remove) a launchd job that publishes the export hourly at 40 past, half an
    hour after the paper runs. A run that changes nothing commits nothing. Written, not
    loaded: the command to load it is printed."""
    label = "com.kestrel.paper-export"
    path = agents_dir / f"{label}.plist"
    if remove:
        path.unlink(missing_ok=True)
        typer.echo(f"Removed {path}. If it was loaded: launchctl bootout gui/$(id -u)/{label}")
        return
    kestrel_bin = Path(sys.executable).parent / "kestrel"
    command = (
        f"exec {shlex.quote(str(kestrel_bin))} paper export --push "
        f"--repo {shlex.quote(str(repo.resolve()))} "
        f"--data-dir {shlex.quote(str(data_dir.resolve()))} "
        f"--config {shlex.quote(str(config.resolve()))}"
    )
    log = data_dir.resolve() / "paper" / "export.log"
    job = {
        "Label": label,
        "ProgramArguments": ["/bin/zsh", "-c", command],
        "StartCalendarInterval": [{"Minute": 40}],
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    agents_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(job))
    typer.echo(f"Wrote {path}: publishes hourly at 40 past, logging to {log}")
    typer.echo(f"Load it with:  launchctl bootstrap gui/$(id -u) {shlex.quote(str(path))}")


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
