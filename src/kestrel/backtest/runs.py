"""Running a backtest end to end, and keeping every run.

``execute`` loads bars, runs the strategy and the benchmark (buy-and-hold of the same
market on the same bars and cash, through the same engine and costs), and computes
metrics for both. A cross-sectional strategy runs on the whole coin universe through
the portfolio engine, against buy-and-hold of BTC over the same period.
``RunStore`` saves each run so results survive restarts and can be compared:

    <data>/runs/<run id>/
        run.json                 parameters, metrics, full config snapshot, code version
        equity.parquet           strategy equity per bar
        benchmark.parquet        benchmark equity per bar
        fills.parquet            every fill with its cost breakdown
        rejections.parquet       every order the simulator refused, with the reason
        trades.parquet           round trips (bracket strategies), with results in R

Runs are written to a temporary directory and renamed into place, so a crash never
leaves a half-saved run. Money is stored as float64 here, for display and analysis;
the engine itself computes in exact decimals.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import polars as pl

from kestrel.backtest.analysis import Analysis, analyse
from kestrel.backtest.engine import BacktestResult, run_backtest
from kestrel.backtest.metrics import Metrics, compute_metrics
from kestrel.backtest.portfolio import run_portfolio
from kestrel.backtest.vectorised import run_vectorised
from kestrel.config import Config
from kestrel.data.binance_vision import Month
from kestrel.data.features import MARKET_WIDE, attach_features
from kestrel.data.panel import load_panel
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.data.universe import manifest_path
from kestrel.strategy import (
    UNIVERSE,
    BracketStrategy,
    BuyAndHold,
    Strategy,
    make_portfolio_strategy,
    make_strategy,
    resolve_params,
    spec_for,
)

UNIVERSE_BENCHMARK = "BTCUSDT"

RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


@dataclass(frozen=True)
class RunRequest:
    strategy: str
    """Key in ``kestrel.strategy.STRATEGIES``."""
    symbol: str
    interval: Interval
    start: Month
    end: Month
    """Inclusive."""
    initial_cash: Decimal
    allow_holdout: bool = False
    params: dict[str, int] = field(default_factory=dict)
    """Strategy parameters; defaults are filled in from the strategy's spec."""
    research: tuple[str, int] | None = None
    """(research slug, note version) this run tests, making it a logged trial."""
    engine: Literal["event", "vectorised"] = "event"
    """The event engine is the reference; the vectorised one is for fast research."""


@dataclass(frozen=True)
class RunOutcome:
    request: RunRequest
    result: BacktestResult
    metrics: Metrics
    benchmark: BacktestResult
    benchmark_metrics: Metrics
    analysis: Analysis
    universe_coins: int | None = None
    """Coins in the panel, for a cross-sectional run."""


def execute(request: RunRequest, store: ParquetStore, config: Config) -> RunOutcome:
    if spec_for(request.strategy).universe or request.symbol.upper() == UNIVERSE:
        return _execute_universe(request, store, config)
    symbol = request.symbol.upper()
    strategy = make_strategy(request.strategy, symbol, request.params)
    after = request.end.next()
    bars = store.read(
        symbol,
        request.interval,
        datetime(request.start.year, request.start.month, 1, tzinfo=UTC),
        datetime(after.year, after.month, 1, tzinfo=UTC),
    )
    if bars.height < 2:
        raise ValueError(
            f"no stored {symbol} {request.interval.value} bars for "
            f"{request.start}..{request.end}; ingest them first"
        )
    bars = attach_features(
        bars, spec_for(request.strategy).features, symbol, request.interval, store.root
    )

    engine = run_vectorised if request.engine == "vectorised" else run_backtest

    def run(s: Strategy | BracketStrategy) -> BacktestResult:
        return engine(
            s,
            bars,
            request.interval,
            config,
            request.initial_cash,
            allow_holdout=request.allow_holdout,
        )

    result = run(strategy)
    benchmark = result if isinstance(strategy, BuyAndHold) else run(BuyAndHold(symbol))
    return RunOutcome(
        request,
        result,
        compute_metrics(result),
        benchmark,
        compute_metrics(benchmark),
        analyse(result, benchmark),
    )


def universe_panel(
    store: ParquetStore, interval: Interval, start: Month, end: Month
) -> pl.DataFrame:
    """Every stored coin in the universe manifest, ``start..end``."""
    path = manifest_path(store, interval)
    if not path.exists():
        raise ValueError(
            f"no {interval.value} coin universe stored; run "
            f"kestrel ingest-universe --interval {interval.value} first"
        )
    coins = sorted(json.loads(path.read_text())["coins"])
    panel = load_panel(store, coins, interval, start, end)
    if panel.is_empty():
        raise ValueError(f"no universe bars stored for {start}..{end}")
    return panel


def strategy_panel(
    store: ParquetStore, key: str, interval: Interval, start: Month, end: Month
) -> pl.DataFrame:
    """The panel a universe strategy runs on: its named assets if it has them (each
    ingested on its own, like PAXGUSDT), else the coin universe; with its market-wide
    features attached."""
    spec = spec_for(key)
    if spec.assets:
        panel = load_panel(store, list(spec.assets), interval, start, end)
        missing = sorted(set(spec.assets) - set(panel["symbol"].unique().to_list()))
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"no {interval.value} bars stored for {names}; ingest them first")
    else:
        panel = universe_panel(store, interval, start, end)
    if spec.features:
        coinwise = sorted(set(spec.features) - set(MARKET_WIDE))
        if coinwise:
            raise ValueError(f"a coin panel can only carry market-wide features, not {coinwise}")
        panel = attach_features(panel, spec.features, UNIVERSE, interval, store.root)
    return panel


def _execute_universe(request: RunRequest, store: ParquetStore, config: Config) -> RunOutcome:
    strategy = make_portfolio_strategy(request.strategy, request.params)
    if request.symbol.upper() != UNIVERSE:
        raise ValueError(f"{request.strategy} runs on {UNIVERSE}, not {request.symbol}")
    if request.engine != "event":
        raise ValueError("cross-sectional strategies run on the event engine only")
    panel = strategy_panel(store, request.strategy, request.interval, request.start, request.end)
    result = run_portfolio(
        strategy,
        panel,
        request.interval,
        config,
        request.initial_cash,
        allow_holdout=request.allow_holdout,
    )
    first, last = panel["open_time"].min(), panel["open_time"].max()
    btc = store.read(UNIVERSE_BENCHMARK, request.interval).filter(
        pl.col("open_time").is_between(first, last)
    )
    if btc.height < 2:
        raise ValueError(f"the benchmark needs {UNIVERSE_BENCHMARK} {request.interval.value} bars")
    benchmark = run_backtest(
        BuyAndHold(UNIVERSE_BENCHMARK),
        btc,
        request.interval,
        config,
        request.initial_cash,
        allow_holdout=request.allow_holdout,
    )
    return RunOutcome(
        request,
        result,
        compute_metrics(result),
        benchmark,
        compute_metrics(benchmark),
        analyse(result, benchmark),
        universe_coins=panel["symbol"].n_unique(),
    )


def _json_safe(value: Any) -> Any:
    """NaN and infinities become None; tuples become lists."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    return value


def metrics_dict(m: Metrics) -> dict[str, Any]:
    """JSON-safe metrics: NaN becomes None, durations become days."""
    out: dict[str, Any] = {}
    for key, value in asdict(m).items():
        if isinstance(value, float) and math.isnan(value):
            value = None
        elif isinstance(value, timedelta):
            key, value = f"{key}_days", value.total_seconds() / 86400
        elif isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, tuple):
            value = [None if math.isnan(v) else v for v in value]
        out[key] = value
    out["total_costs"] = m.total_costs
    out["few_trades"] = m.few_trades
    return out


class RunStore:
    def __init__(self, data_dir: Path | str) -> None:
        self.root = Path(data_dir) / "runs"

    def save(self, outcome: RunOutcome, config: Config) -> str:
        created = datetime.now(UTC)
        run_id = f"{created:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        req, res = outcome.request, outcome.result
        meta = {
            "id": run_id,
            "created_at": created.isoformat(),
            "strategy": req.strategy,
            "strategy_kind": spec_for(req.strategy).kind,
            "style": res.style,
            "engine": res.engine,
            "strategy_name": res.strategy,
            "params": resolve_params(req.strategy, req.params),
            "research": (
                {"slug": req.research[0], "version": req.research[1]} if req.research else None
            ),
            "symbol": res.symbol,
            "benchmark_symbol": outcome.benchmark.symbol,
            "universe_coins": outcome.universe_coins,
            "forced_exits": res.forced_exits,
            "interval": req.interval.value,
            "start": str(req.start),
            "end": str(req.end),
            "initial_cash": str(req.initial_cash),
            "account_currency": config.account_currency,
            "used_holdout": res.used_holdout,
            "config_fingerprint": res.config_fingerprint,
            "config": config.document,
            "code": asdict(res.code) if res.code else None,
            "metrics": metrics_dict(outcome.metrics),
            "benchmark_metrics": metrics_dict(outcome.benchmark_metrics),
            "analysis": _json_safe(asdict(outcome.analysis)),
        }

        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{run_id}.tmp"
        tmp.mkdir()
        try:
            (tmp / "run.json").write_text(json.dumps(meta, indent=2, default=str))
            res.equity.write_parquet(tmp / "equity.parquet")
            outcome.benchmark.equity.write_parquet(tmp / "benchmark.parquet")
            _fills_frame(res).write_parquet(tmp / "fills.parquet")
            _rejections_frame(res).write_parquet(tmp / "rejections.parquet")
            _trades_frame(res).write_parquet(tmp / "trades.parquet")
            tmp.rename(self.root / run_id)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return run_id

    def summaries(self) -> list[dict[str, Any]]:
        """Saved runs, newest first (without the config snapshot)."""
        runs = []
        for path in self.root.glob("*/run.json"):
            if RUN_ID.match(path.parent.name):
                meta = json.loads(path.read_text())
                meta.pop("config", None)
                runs.append(meta)
        return sorted(runs, key=lambda r: r["created_at"], reverse=True)

    def trials(self, slug: str) -> list[dict[str, Any]]:
        """Runs logged against a research strategy, oldest first."""
        runs = [r for r in self.summaries() if (r.get("research") or {}).get("slug") == slug]
        return sorted(runs, key=lambda r: r["created_at"])

    def load(self, run_id: str) -> SavedRun:
        if not RUN_ID.match(run_id) or not (self.root / run_id / "run.json").exists():
            raise KeyError(run_id)
        d = self.root / run_id
        return SavedRun(
            meta=json.loads((d / "run.json").read_text()),
            equity=pl.read_parquet(d / "equity.parquet"),
            benchmark=pl.read_parquet(d / "benchmark.parquet"),
            fills=pl.read_parquet(d / "fills.parquet"),
            rejections=pl.read_parquet(d / "rejections.parquet"),
            # Runs saved before stops existed have no trades file.
            trades=(
                pl.read_parquet(d / "trades.parquet")
                if (d / "trades.parquet").exists()
                else _trades_frame(None)
            ),
        )


@dataclass(frozen=True)
class SavedRun:
    meta: dict[str, Any]
    equity: pl.DataFrame
    benchmark: pl.DataFrame
    fills: pl.DataFrame
    rejections: pl.DataFrame
    trades: pl.DataFrame


def _fills_frame(res: BacktestResult) -> pl.DataFrame:
    schema: dict[str, pl.DataType] = {
        "time": pl.Datetime("us", "UTC"),
        "side": pl.Utf8(),
        "quantity": pl.Float64(),
        "reference_price": pl.Float64(),
        "price": pl.Float64(),
        "fee": pl.Float64(),
        "spread_cost": pl.Float64(),
        "impact_cost": pl.Float64(),
        "cash_flow": pl.Float64(),
    }
    rows = [
        {
            "time": r.time,
            "side": r.fill.side.value,
            "quantity": float(r.fill.quantity),
            "reference_price": float(r.fill.reference_price),
            "price": float(r.fill.price),
            "fee": float(r.fill.fee),
            "spread_cost": float(r.fill.spread_cost),
            "impact_cost": float(r.fill.impact_cost),
            "cash_flow": float(r.fill.cash_flow),
        }
        for r in res.fills
    ]
    return pl.DataFrame(rows, schema=schema)


def _rejections_frame(res: BacktestResult) -> pl.DataFrame:
    schema: dict[str, pl.DataType] = {
        "time": pl.Datetime("us", "UTC"),
        "side": pl.Utf8(),
        "quantity": pl.Float64(),
        "reason": pl.Utf8(),
    }
    rows = [
        {
            "time": r.time,
            "side": r.rejection.side.value,
            "quantity": float(r.rejection.requested_quantity),
            "reason": r.rejection.reason,
        }
        for r in res.rejections
    ]
    return pl.DataFrame(rows, schema=schema)


def trial_key(run: dict[str, Any]) -> tuple[Any, ...]:
    """What makes two runs the same trial: identical note version, implementation,
    parameters, market, period and cash. Re-running an identical configuration is not a
    new trial; any change to it is. A code fix that leaves these unchanged doesn't add a
    trial, which is why a note version pins its implementation. P3's deflated Sharpe
    uses the count of distinct trials."""
    research = run.get("research") or {}
    return (
        research.get("version"),
        run["strategy"],
        tuple(sorted((run.get("params") or {}).items())),
        run["symbol"],
        run["interval"],
        run["start"],
        run["end"],
        run["initial_cash"],
    )


def count_trials(runs: list[dict[str, Any]]) -> int:
    return len({trial_key(r) for r in runs})


TRADE_SCHEMA: dict[str, pl.DataType] = {
    "entry_time": pl.Datetime("us", "UTC"),
    "exit_time": pl.Datetime("us", "UTC"),
    "exit_reason": pl.Utf8(),
    "quantity": pl.Float64(),
    "entry_price": pl.Float64(),
    "exit_price": pl.Float64(),
    "stop": pl.Float64(),
    "target": pl.Float64(),
    "risk": pl.Float64(),
    "pnl": pl.Float64(),
    "costs": pl.Float64(),
    "r_multiple": pl.Float64(),
    "cost_r": pl.Float64(),
    "risk_capped": pl.Boolean(),
}


def _trades_frame(res: BacktestResult | None) -> pl.DataFrame:
    rows = [
        {
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "exit_reason": t.exit_reason,
            "quantity": float(t.quantity),
            "entry_price": float(t.entry_price),
            "exit_price": float(t.exit_price),
            "stop": float(t.stop),
            "target": None if t.target is None else float(t.target),
            "risk": float(t.risk),
            "pnl": float(t.pnl),
            "costs": float(t.costs),
            "r_multiple": float(t.r_multiple),
            "cost_r": float(t.cost_r),
            "risk_capped": t.risk_capped,
        }
        for t in (res.trades if res else [])
    ]
    return pl.DataFrame(rows, schema=TRADE_SCHEMA)
