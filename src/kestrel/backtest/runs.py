"""Running a backtest end to end, and keeping every run.

``execute`` loads bars, runs the strategy and the benchmark (BTC buy-and-hold on the
same bars and cash, through the same engine and costs), and computes metrics for both.
``RunStore`` saves each run so results survive restarts and can be compared:

    <data>/runs/<run id>/
        run.json                 parameters, metrics, full config snapshot, code version
        equity.parquet           strategy equity per bar
        benchmark.parquet        benchmark equity per bar
        fills.parquet            every fill with its cost breakdown
        rejections.parquet       every order the simulator refused, with the reason

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
from typing import Any

import polars as pl

from kestrel.backtest.engine import BacktestResult, run_backtest
from kestrel.backtest.metrics import Metrics, compute_metrics
from kestrel.config import Config
from kestrel.data.binance_vision import Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategy import (
    BracketStrategy,
    BuyAndHold,
    Strategy,
    make_strategy,
    resolve_params,
    spec_for,
)

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


@dataclass(frozen=True)
class RunOutcome:
    request: RunRequest
    result: BacktestResult
    metrics: Metrics
    benchmark: BacktestResult
    benchmark_metrics: Metrics


def execute(request: RunRequest, store: ParquetStore, config: Config) -> RunOutcome:
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

    def run(s: Strategy | BracketStrategy) -> BacktestResult:
        return run_backtest(
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
        request, result, compute_metrics(result), benchmark, compute_metrics(benchmark)
    )


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
            "strategy_name": res.strategy,
            "params": resolve_params(req.strategy, req.params),
            "research": (
                {"slug": req.research[0], "version": req.research[1]} if req.research else None
            ),
            "symbol": res.symbol,
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
    """What makes two runs the same trial: identical version, code, parameters, market,
    period and cash. Re-running an identical configuration is not a new trial; any
    change to it is. P3's deflated Sharpe uses the count of distinct trials."""
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
            "target": float(t.target),
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
