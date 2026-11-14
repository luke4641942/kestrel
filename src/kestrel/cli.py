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
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import httpx
import polars as pl
import typer

from kestrel.backtest.engine import HoldoutError, run_backtest
from kestrel.backtest.metrics import Metrics, compute_metrics, format_report
from kestrel.backtest.portfolio import run_portfolio
from kestrel.backtest.runs import RunRequest, RunStore, execute, universe_panel
from kestrel.config import ConfigError, load_config
from kestrel.data import funding, sentiment
from kestrel.data.binance_vision import BinanceVisionClient, Month, last_complete_month
from kestrel.data.features import attach_features
from kestrel.data.quality import check_klines
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.data.universe import ArchiveListing, CoinIngest, ingest_universe, manifest_path
from kestrel.regimes import format_regimes
from kestrel.regimes import walk_forward as walk_regimes
from kestrel.research import ResearchStore, run_target
from kestrel.strategy import (
    STRATEGIES,
    make_portfolio_strategy,
    make_strategy,
    resolve_params,
    spec_for,
)
from kestrel.studies.anchor_day import format_study, run_study
from kestrel.validation.holdout import HoldoutLog, format_holdout, holdout_test
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
) -> None:
    """Backtest a strategy on stored bars and print its metrics."""
    if engine not in ("event", "vectorised"):
        raise typer.BadParameter("engine must be event or vectorised")
    notes = ResearchStore(research_dir)
    try:
        key, link = run_target(notes, strategy, research)
        params = resolve_params(key, _parse_params(param or []))
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
    )
    try:
        cfg = load_config(config)
        outcome = execute(request, ParquetStore(data_dir), cfg)
    except (HoldoutError, ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        format_report(outcome.result, outcome.metrics, outcome.analysis, outcome.benchmark.asset)
    )
    if save:
        typer.echo(f"\nSaved as run {RunStore(data_dir).save(outcome, cfg)}")
        if link:
            notes.mark_backtested(*link)
            typer.echo(f"Logged as a trial of {link[0]} v{link[1]}")


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
        bars = ParquetStore(data_dir).read(
            symbol, interval, datetime(first.year, first.month, 1, tzinfo=UTC), until
        )
        bars = attach_features(bars, spec_for(key).features, symbol, interval, data_dir)
        typer.echo(f"HOLDOUT USED: {key} on {symbol.upper()} for {link[0]} v{link[1]}")
        result = holdout_test(
            key,
            symbol.upper(),
            bars,
            interval,
            cfg,
            Decimal(str(cash)),
            resolve_params(key, {}),
            start=begin,
            end=until,
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
            Decimal(str(cash)),
            values,
            start=first,
            end=last,
            train_months=train_months,
            test_months=test_months,
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
            panel = universe_panel(store, interval, first, last)

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
    typer.echo(f"Kestrel dashboard: http://{host}:{port}   API docs: http://{host}:{port}/api/docs")
    uvicorn.run("kestrel.api.server:app", host=host, port=port, reload=reload, factory=False)


if __name__ == "__main__":
    app()
