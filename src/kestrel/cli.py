"""Command-line entry point.

kestrel ingest BTCUSDT --interval 1h --start 2020-01
kestrel check BTCUSDT --interval 1h
kestrel list BTCUSDT --interval 1h
kestrel backtest BTCUSDT --interval 4h --start 2022-01 --end 2025-10
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import polars as pl
import typer

from kestrel.backtest.engine import HoldoutError
from kestrel.backtest.metrics import format_report
from kestrel.backtest.runs import RunRequest, RunStore, execute
from kestrel.config import ConfigError, load_config
from kestrel.data.binance_vision import BinanceVisionClient, Month, last_complete_month
from kestrel.data.quality import check_klines
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategy import STRATEGIES

app = typer.Typer(help="Kestrel trading platform.", no_args_is_help=True)

DataDir = Annotated[Path, typer.Option("--data-dir", envvar="KESTREL_DATA", help="Data root.")]
IntervalOpt = Annotated[Interval, typer.Option("--interval", "-i")]


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
    if result.frames:
        store.write(symbol, interval, pl.concat(result.frames))
    typer.echo(f"fetched {len(result.fetched)} month(s) of {symbol.upper()} {interval.value}")
    if result.not_found:
        typer.echo(f"not published by Binance: {', '.join(map(str, result.not_found))}")


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
) -> None:
    """Backtest a strategy on stored bars and print its metrics."""
    if strategy not in STRATEGIES:
        raise typer.BadParameter(f"unknown strategy {strategy!r}; one of {', '.join(STRATEGIES)}")
    request = RunRequest(
        strategy=strategy,
        symbol=symbol,
        interval=interval,
        start=Month.parse(start),
        end=Month.parse(end),
        initial_cash=Decimal(str(cash)),
        allow_holdout=allow_holdout,
    )
    try:
        cfg = load_config(config)
        outcome = execute(request, ParquetStore(data_dir), cfg)
    except (HoldoutError, ConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(format_report(outcome.result, outcome.metrics))
    if save:
        typer.echo(f"\nSaved as run {RunStore(data_dir).save(outcome, cfg)}")


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
) -> None:
    """Run the API and dashboard at http://HOST:PORT."""
    import os

    import uvicorn

    os.environ["KESTREL_DATA"] = str(data_dir)
    os.environ["KESTREL_CONFIG"] = str(config)
    typer.echo(f"Kestrel dashboard: http://{host}:{port}   API docs: http://{host}:{port}/api/docs")
    uvicorn.run("kestrel.api.server:app", host=host, port=port, reload=reload, factory=False)


if __name__ == "__main__":  # pragma: no cover - entry-point guard
    app()
