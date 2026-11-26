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

from kestrel.backtest import warmup
from kestrel.backtest.analysis import Analysis, analyse
from kestrel.backtest.engine import BacktestResult, run_backtest
from kestrel.backtest.metrics import Metrics, compute_metrics
from kestrel.backtest.portfolio import run_portfolio
from kestrel.backtest.vectorised import run_vectorised
from kestrel.config import Config
from kestrel.data.binance_vision import Month
from kestrel.data.features import MARKET_WIDE, attach_features
from kestrel.data.panel import load_panel, refuse_gaps
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
    confirmation: bool = False
    """A frozen confirmation note's run in the exploration experiment: its own trial pool."""


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
    loaded = coin_bars(
        store, request.strategy, symbol, request.interval, request.start, request.end
    )
    history, bars = warm_split(loaded, request.strategy, request.params, request.start)
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

    if isinstance(strategy, BuyAndHold):
        result = benchmark = run(strategy)
    else:
        result = run(warmup.warmed(strategy, history))
        benchmark = run(BuyAndHold(symbol))
    result.history_from = history["open_time"][0] if history.height else None
    return RunOutcome(
        request,
        result,
        compute_metrics(result),
        benchmark,
        compute_metrics(benchmark),
        analyse(result, benchmark),
    )


def coin_bars(
    store: ParquetStore, key: str, symbol: str, interval: Interval, start: Month, end: Month
) -> pl.DataFrame:
    """One coin's bars for ``start..end`` plus the history before it a strategy may read
    (kestrel.backtest.warmup), with ``key``'s features attached. A gap anywhere in them is
    refused: the history is read as much as the scored bars are (findings 31)."""
    first, after = _first_instant(start), end.next()
    loaded = store.read(
        symbol.upper(),
        interval,
        warmup.history_start(first),
        datetime(after.year, after.month, 1, tzinfo=UTC),
    )
    if loaded.filter(pl.col("open_time") >= first).height < 2:
        raise ValueError(
            f"no stored {symbol.upper()} {interval.value} bars for {start}..{end}; "
            "ingest them first"
        )
    refuse_gaps(loaded, interval, symbol.upper())
    return attach_features(loaded, spec_for(key).features, symbol.upper(), interval, store.root)


def scored_start(loaded: pl.DataFrame, key: str, params: dict[str, int], start: Month) -> datetime:
    """The first bar ``key`` is scored on: ``start``, or later on data that begins too late
    for its lookback (kestrel.backtest.warmup)."""
    days = warmup.warmup_days(key, resolve_params(key, params))
    return warmup.scored_from(loaded["open_time"].min(), _first_instant(start), days)  # type: ignore[arg-type]


def warm_split(
    loaded: pl.DataFrame,
    key: str,
    params: dict[str, int],
    start: Month,
    at: datetime | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(history ``key`` reads, the bars it is scored on): from ``scored_start``, or from
    ``at`` when the caller scores several settings over the same days (never earlier than
    the setting's own start)."""
    own = scored_start(loaded, key, params, start)
    history, bars = warmup.split(loaded, max(own, at) if at else own)
    if bars["open_time"].n_unique() < 2:
        raise ValueError(f"the bars end before {key}'s warm-up has filled")
    return history, bars


def _first_instant(month: Month) -> datetime:
    return datetime(month.year, month.month, 1, tzinfo=UTC)


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
    manifest = json.loads(path.read_text())
    if "tokenized_source" not in manifest:
        raise ValueError(
            f"the {interval.value} coin universe predates the tokenized-stock check; re-run "
            f"kestrel ingest-universe --interval {interval.value} (stored months are skipped)"
        )
    coins = sorted(manifest["coins"])
    panel = load_panel(store, coins, interval, start, end)
    if panel.is_empty():
        raise ValueError(f"no universe bars stored for {start}..{end}")
    return panel


def visible(panel: pl.DataFrame, config: Config, key: str) -> pl.DataFrame:
    """A universe panel without the coins of the locked confirmation half: a run over the
    whole market sees only the coins it may (the engine refuses the rest). A strategy over
    named assets keeps them (and is refused if one is locked)."""
    if config.split is None or config.confirmation_unlocked or spec_for(key).assets:
        return panel
    return panel.filter(~pl.col("symbol").is_in(sorted(config.split.confirmation)))


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
    if spec.coin_events:
        from kestrel.data import coin_events

        panel = coin_events.attach_stored(panel, store.root)
    return panel


def universe_bars(
    store: ParquetStore, config: Config, key: str, interval: Interval, start: Month, end: Month
) -> pl.DataFrame:
    """A universe strategy's visible panel for ``start..end`` plus the history before it
    (kestrel.backtest.warmup)."""
    first = _first_instant(start)
    since = warmup.history_start(first)
    panel = visible(
        strategy_panel(store, key, interval, Month.parse(f"{since:%Y-%m}"), end), config, key
    ).filter(pl.col("open_time") >= since)
    if panel.filter(pl.col("open_time") >= first).is_empty():
        raise ValueError(f"no universe bars stored for {start}..{end}")
    return panel


def _execute_universe(request: RunRequest, store: ParquetStore, config: Config) -> RunOutcome:
    strategy = make_portfolio_strategy(request.strategy, request.params)
    if request.symbol.upper() != UNIVERSE:
        raise ValueError(f"{request.strategy} runs on {UNIVERSE}, not {request.symbol}")
    if request.engine != "event":
        raise ValueError("cross-sectional strategies run on the event engine only")
    loaded = universe_bars(
        store, config, request.strategy, request.interval, request.start, request.end
    )
    history, panel = warm_split(loaded, request.strategy, request.params, request.start)
    result = run_portfolio(
        warmup.warmed_portfolio(strategy, history),
        panel,
        request.interval,
        config,
        request.initial_cash,
        allow_holdout=request.allow_holdout,
    )
    result.history_from = history["open_time"].min() if history.height else None  # type: ignore[assignment]
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
            "scored_from": res.start.isoformat() if res.start else None,
            "history_from": res.history_from.isoformat() if res.history_from else None,
            "initial_cash": str(req.initial_cash),
            "account_currency": config.account_currency,
            "used_holdout": res.used_holdout,
            "confirmation": req.confirmation,
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


def same_setup_earlier(runs: list[dict[str, Any]], request: RunRequest) -> dict[str, Any] | None:
    """A saved run of exactly this setup (implementation, settings, market, period, cash)
    under an older version of the same note, if there is one. Saved again under the new
    version it would count as a new trial (the version is in ``trial_key``), so a caller
    refuses it unless asked for a new trial (findings 33: two reruns were nearly counted)."""
    if request.research is None:
        return None
    slug, version = request.research
    for run in runs:
        theirs = run.get("research") or {}
        if (
            theirs.get("slug") == slug
            and (theirs.get("version") or 0) < version
            and run["strategy"] == request.strategy
            and (run.get("params") or {}) == resolve_params(request.strategy, request.params)
            and run["symbol"] == request.symbol.upper()
            and run["interval"] == request.interval.value
            and run["start"] == str(request.start)
            and run["end"] == str(request.end)
            and Decimal(run["initial_cash"]) == request.initial_cash
        ):
            return run
    return None


def repeat_message(run: dict[str, Any], version: int) -> str:
    old = run["research"]
    return (
        f"run {run['id']} already tested this exact setup under v{old['version']} of "
        f"{old['slug']}; saved under v{version} it would count as a new trial. Rerun with "
        f"--as-new-trial only if v{version} changed what this setup does."
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
