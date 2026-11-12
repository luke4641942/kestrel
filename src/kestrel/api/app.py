"""HTTP API over the platform, plus the built dashboard as static files.

The engine never imports anything from here: the API is a thin adapter that
calls platform functions, so the same operations stay available from the CLI,
scripts and tests without a web server.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import APIRouter, FastAPI, HTTPException, Query, status
from fastapi.staticfiles import StaticFiles

from kestrel import __version__
from kestrel.api.jobs import JobContext, JobError, JobManager
from kestrel.api.models import (
    BacktestRequest,
    Bars,
    ChatMessage,
    ChatRequest,
    CostConfigOut,
    CostCurve,
    CreateResearch,
    CurvePoint,
    Dataset,
    EditNote,
    FillOut,
    Health,
    IngestRequest,
    JobOut,
    NoteOut,
    ParamOut,
    QualityOut,
    Quote,
    Reason,
    RejectionOut,
    ResearchDetail,
    ResearchSummary,
    RunDetail,
    RunSummary,
    Series,
    Signals,
    SignalsRequest,
    SourceOut,
    StatusChangeOut,
    StrategyOut,
    StudyOut,
    TradeOut,
    ValidationOut,
)
from kestrel.assistant import AssistantError, ResearchAssistant, chat_record, credentials_hint
from kestrel.backtest.costs import CostModel, Fill, Side
from kestrel.backtest.engine import HoldoutError
from kestrel.backtest.metrics import DAYS_PER_YEAR
from kestrel.backtest.runs import RunRequest, RunStore, count_trials, execute
from kestrel.config import Config, ConfigError, InstrumentRules, load_config
from kestrel.data.binance_vision import BinanceVisionClient, Month, last_complete_month
from kestrel.data.quality import check_klines
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.provenance import code_version
from kestrel.research import (
    SECTIONS,
    ResearchError,
    ResearchStore,
    ResearchStrategy,
    run_target,
)
from kestrel.strategy import (
    STRATEGIES,
    BracketStrategy,
    StrategySpec,
    make_strategy,
    resolve_params,
    strategy_source,
    validated_brackets,
    validated_weights,
)
from kestrel.validation.deflated import TrialSet, counts_as_trial, deflated_sharpe, trial_set
from kestrel.validation.log import ValidationLog

STATIC_DIR = Path(__file__).parent / "static"
MAX_BARS = 20_000

ClientFactory = Callable[[], BinanceVisionClient]


def create_app(
    data_dir: Path | str = "data",
    client_factory: ClientFactory = BinanceVisionClient,
    static_dir: Path | None = STATIC_DIR,
    config_path: Path | str = "kestrel.toml",
    research_dir: Path | str = "research",
    assistant: ResearchAssistant | None = None,
) -> FastAPI:
    store = ParquetStore(data_dir)
    runs = RunStore(data_dir)
    studies = ValidationLog(data_dir)
    research = ResearchStore(research_dir)
    jobs = JobManager()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        jobs.shutdown()

    app = FastAPI(
        title="Kestrel",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    api = APIRouter(prefix="/api")

    # ---------- system ----------

    @api.get("/health")
    def health() -> Health:
        return Health(status="ok", version=__version__, data_dir=str(store.root.resolve()))

    # ---------- data ----------

    def _require(symbol: str, interval: Interval) -> str:
        symbol = symbol.upper()
        if not store.months(symbol, interval):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no data for {symbol} {interval}")
        return symbol

    @api.get("/datasets")
    def list_datasets() -> list[Dataset]:
        out = []
        for symbol, interval in store.datasets():
            stats = (
                store.scan(symbol, interval)
                .select(
                    pl.len().alias("rows"),
                    pl.col("open_time").min().alias("first"),
                    pl.col("open_time").max().alias("last"),
                )
                .collect()
                .row(0, named=True)
            )
            out.append(
                Dataset(
                    venue=store.venue,
                    symbol=symbol,
                    interval=interval,
                    months=len(store.months(symbol, interval)),
                    **stats,
                )
            )
        return out

    @api.get("/datasets/{symbol}/{interval}/bars")
    def get_bars(
        symbol: str,
        interval: Interval,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = Query(default=2_000, ge=1, le=MAX_BARS),
    ) -> Bars:
        """Bars in ``[start, end)``; without a start, the most recent ``limit`` bars."""
        symbol = _require(symbol, interval)
        df = store.read(symbol, interval, start, end)
        df = df.head(limit) if start is not None else df.tail(limit)
        return Bars(
            symbol=symbol,
            interval=interval,
            time=(df["open_time"].dt.epoch("s")).to_list(),
            open=df["open"].to_list(),
            high=df["high"].to_list(),
            low=df["low"].to_list(),
            close=df["close"].to_list(),
            volume=df["volume"].to_list(),
        )

    @api.get("/datasets/{symbol}/{interval}/quality")
    def get_quality(symbol: str, interval: Interval) -> QualityOut:
        symbol = _require(symbol, interval)
        report = check_klines(store.read(symbol, interval), interval)
        return QualityOut.from_report(symbol, interval, report)

    # ---------- costs ----------
    # Read-only views of the backtester's cost model. The config is re-read per request,
    # so edits to kestrel.toml show up without a restart and a broken file is a clear 503.

    def _config() -> Config:
        try:
            return load_config(config_path)
        except ConfigError as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from None

    def _rules(config: Config, symbol: str) -> InstrumentRules:
        try:
            return config.instrument(symbol)
        except ConfigError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from None

    @api.get("/costs")
    def get_costs() -> CostConfigOut:
        return CostConfigOut.from_config(_config())

    @api.get("/costs/quote")
    def quote_order(
        notional: float = Query(gt=0, description="Order size in the account currency"),
        price: float = Query(gt=0, description="Reference price before costs"),
        bar_volume: float = Query(ge=0, description="Quote volume of the last closed bar"),
        symbol: str = "BTCUSDT",
        side: Side = Side.BUY,
    ) -> Quote:
        """Simulate one market order through the backtester's cost model."""
        config = _config()
        rules = _rules(config, symbol)
        px, vol = Decimal(str(price)), Decimal(str(bar_volume))
        result = CostModel(config.costs).fill_market(
            side, Decimal(str(notional)) / px, px, vol, rules
        )
        return Quote.from_result(result, px, vol)

    @api.get("/costs/curve")
    def cost_curve(
        price: float = Query(gt=0),
        bar_volume: float = Query(gt=0),
        symbol: str = "BTCUSDT",
        side: Side = Side.BUY,
        points: int = Query(default=160, ge=2, le=1000),
    ) -> CostCurve:
        """Cost in bps across the order sizes the simulator would fill, log-spaced."""
        config = _config()
        rules = _rules(config, symbol)
        model = CostModel(config.costs)
        px, vol = Decimal(str(price)), Decimal(str(bar_volume))
        lo = float(rules.min_notional) or 1.0
        hi = float(config.costs.max_participation * vol)
        out: list[CurvePoint] = []
        for i in range(points):
            target = lo * (hi / lo) ** (i / (points - 1)) if hi > lo else lo
            fill = model.fill_market(side, Decimal(f"{target:.10g}") / px, px, vol, rules)
            if not isinstance(fill, Fill):
                continue  # e.g. rounding down to the step pushed it under the minimum
            n = fill.quantity * fill.reference_price
            per_bps = Decimal(10_000) / n
            out.append(
                CurvePoint(
                    notional=float(n),
                    fee_bps=float(fill.fee * per_bps),
                    spread_bps=float(fill.spread_cost * per_bps),
                    impact_bps=float(fill.impact_cost * per_bps),
                    total_bps=float(fill.total_cost * per_bps),
                )
            )
        return CostCurve(
            symbol=rules.symbol,
            side=side,
            reference_price=price,
            bar_volume=bar_volume,
            min_notional=float(rules.min_notional),
            max_notional=hi,
            points=out,
        )

    # ---------- jobs ----------

    def _ingest(req: IngestRequest) -> Callable[[JobContext], dict[str, Any]]:
        symbol = req.symbol.upper()
        first = Month.parse(req.start)
        last = Month.parse(req.end) if req.end else last_complete_month(datetime.now(UTC).date())

        def run(ctx: JobContext) -> dict[str, Any]:
            ctx.log(f"Ingesting {symbol} {req.interval.value} {first} -> {last}")

            def on_month(month: Month, done: int, total: int) -> None:
                ctx.check_cancelled()
                ctx.progress(done / max(total, 1), f"Downloading {month} ({done + 1}/{total})")

            with client_factory() as client:
                result = client.fetch_range(symbol, req.interval, first, last, on_month)

            if not result.fetched:
                raise JobError(
                    f"Binance publishes no {symbol} {req.interval.value} data for {first}..{last}. "
                    "Check the pair code and that it was listed in that period."
                )

            ctx.progress(0.98, "Writing to store")
            store.write(symbol, req.interval, pl.concat(result.frames))
            for m in result.not_found:
                ctx.log(f"Not published by Binance: {m}")

            report = check_klines(store.read(symbol, req.interval), req.interval)
            ctx.log(
                f"Fetched {len(result.fetched)} month(s); quality: "
                f"{'clean' if report.is_clean else 'FAILED'}, {report.missing_bars} missing bars"
            )
            return {
                "symbol": symbol,
                "interval": req.interval.value,
                "fetched": [str(m) for m in result.fetched],
                "not_found": [str(m) for m in result.not_found],
                "quality_clean": report.is_clean,
                "rows": report.rows,
            }

        return run

    @api.post("/jobs/ingest", status_code=status.HTTP_202_ACCEPTED)
    def start_ingest(req: IngestRequest) -> JobOut:
        if Month.parse(req.start) > (
            Month.parse(req.end) if req.end else last_complete_month(datetime.now(UTC).date())
        ):
            raise HTTPException(422, "start is after end")
        job = jobs.submit("ingest", req.model_dump(mode="json"), _ingest(req))
        return JobOut.from_job(job)

    # ---------- backtests ----------

    @api.get("/strategies")
    def list_strategies() -> list[StrategyOut]:
        return [_strategy_out(spec) for spec in STRATEGIES.values()]

    @api.get("/strategies/{key}/source")
    def get_source(key: str) -> SourceOut:
        try:
            source = strategy_source(key)
        except ValueError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "strategy not found") from None
        version = code_version()
        return SourceOut(key=key, source=source, commit=version.commit, dirty=version.dirty)

    @api.post("/strategies/{key}/signals")
    def get_signals(key: str, req: SignalsRequest) -> Signals:
        """What the strategy would hold after each bar closes, on stored bars."""
        try:
            strategy = make_strategy(key, req.symbol, req.params)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        first, after = Month.parse(req.start), Month.parse(req.end).next()
        bars = store.read(
            req.symbol,
            req.interval,
            datetime(first.year, first.month, 1, tzinfo=UTC),
            datetime(after.year, after.month, 1, tzinfo=UTC),
        )
        if bars.is_empty():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no stored bars in that range")
        try:
            if isinstance(strategy, BracketStrategy):
                kind = "entries"
                weights = validated_brackets(strategy, bars)["enter"].cast(pl.Float64)
            else:
                kind, weights = "weights", validated_weights(strategy, bars)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        # Computed on the whole range, exactly as a backtest would; only drawing is capped.
        total = bars.height
        bars, weights = bars.tail(MAX_BARS), weights.tail(MAX_BARS)
        return Signals(
            kind=kind,
            total_bars=total,
            time=bars["open_time"].dt.epoch("s").to_list(),
            open=bars["open"].to_list(),
            high=bars["high"].to_list(),
            low=bars["low"].to_list(),
            close=bars["close"].to_list(),
            weight=weights.to_list(),
        )

    @api.post("/jobs/backtest", status_code=status.HTTP_202_ACCEPTED)
    def start_backtest(req: BacktestRequest) -> JobOut:
        if Month.parse(req.start) > Month.parse(req.end):
            raise HTTPException(422, "start is after end")
        try:
            key, link = run_target(research, req.strategy, req.research)
            params = resolve_params(key, req.params)
        except ValueError as e:  # ResearchError is a ValueError
            raise HTTPException(422, str(e)) from None
        request = RunRequest(
            strategy=key,
            symbol=req.symbol,
            interval=req.interval,
            start=Month.parse(req.start),
            end=Month.parse(req.end),
            initial_cash=Decimal(str(req.initial_cash)),
            params=params,
            research=link,
        )

        def run(ctx: JobContext) -> dict[str, Any]:
            ctx.log(f"Backtesting {key} {params or ''} on {req.symbol} {req.interval.value}")
            if link:
                ctx.log(f"A trial of {link[0]} v{link[1]}: logged once the run is saved")
            ctx.progress(0.1, "Running strategy and benchmark")
            try:
                config = load_config(config_path)
                outcome = execute(request, store, config)
            except (HoldoutError, ConfigError, ValueError) as e:
                raise JobError(str(e)) from None
            ctx.progress(0.9, "Saving run")
            run_id = runs.save(outcome, config)
            if link:
                research.mark_backtested(*link)
            m = outcome.metrics
            ctx.log(f"Saved run {run_id}: return {m.total_return:+.2%}, {m.trades} trades")
            if link:
                ctx.log(f"Logged as a trial of {link[0]} v{link[1]}")
            return {"run_id": run_id}

        job = jobs.submit("backtest", {**req.model_dump(mode="json"), "strategy": key}, run)
        return JobOut.from_job(job)

    @api.get("/backtests")
    def list_backtests() -> list[RunSummary]:
        return _deflated(runs.summaries())

    @api.get("/validation")
    def get_validation() -> ValidationOut:
        t = trial_set(runs.summaries(), studies.studies())
        return ValidationOut(
            trials=t.count,
            sharpe_std=t.sharpe_std * math.sqrt(DAYS_PER_YEAR),
            expected_max_sharpe=t.expected_max_sharpe_annual,
            studies=[StudyOut.model_validate(x) for x in studies.studies()],
        )

    def _deflated(metas: list[dict[str, Any]], trials: TrialSet | None = None) -> list[RunSummary]:
        """Run summaries with each one's Sharpe deflated for every strategy tried."""
        t = trials or trial_set(runs.summaries(), studies.studies())
        out = []
        for meta in metas:
            m = meta["metrics"]
            dsr = (
                deflated_sharpe(m.get("sharpe"), m["days"], m.get("skew"), m.get("kurtosis"), t)
                if counts_as_trial(meta)
                else None
            )
            out.append(
                RunSummary.model_validate(
                    {
                        **meta,
                        "deflated_sharpe": dsr,
                        "deflated_trials": t.count if dsr is not None else None,
                    }
                )
            )
        return out

    @api.get("/backtests/{run_id}")
    def get_backtest(run_id: str) -> RunDetail:
        try:
            saved = runs.load(run_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found") from None
        (summary,) = _deflated([saved.meta])
        return RunDetail.model_validate(
            {
                **saved.meta,
                "deflated_sharpe": summary.deflated_sharpe,
                "deflated_trials": summary.deflated_trials,
                "equity": _series(saved.equity),
                "benchmark": _series(saved.benchmark),
                "fills": [FillOut.model_validate(r) for r in saved.fills.iter_rows(named=True)],
                "rejections": [
                    RejectionOut.model_validate(r) for r in saved.rejections.iter_rows(named=True)
                ],
                "trades": [TradeOut.model_validate(r) for r in saved.trades.iter_rows(named=True)],
            }
        )

    # ---------- research ----------

    def _summary(s: ResearchStrategy) -> ResearchSummary:
        logged = runs.trials(s.slug)
        scored = [r.deflated_sharpe for r in _deflated(logged) if r.deflated_sharpe is not None]
        return ResearchSummary(
            slug=s.slug,
            name=s.name,
            status=s.status,
            created_at=datetime.fromisoformat(s.created_at),
            version=s.latest.version,
            frozen=s.latest.frozen,
            implementation=s.latest.implementation,
            runs=len(logged),
            trials=count_trials(logged),
            deflated_sharpe=max(scored) if scored else None,
        )

    def _detail(slug: str) -> ResearchDetail:
        try:
            s = research.get(slug)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "research strategy not found") from None
        return ResearchDetail(
            **_summary(s).model_dump(),
            sections=SECTIONS,
            notes=[NoteOut.model_validate(asdict(n)) for n in s.notes],
            history=[StatusChangeOut.model_validate(asdict(h)) for h in s.history],
            trial_log=_deflated(runs.trials(slug)),
            chat=[ChatMessage.model_validate(m) for m in research.chat(slug)],
            assistant_available=assistant is not None,
            assistant_hint=credentials_hint() if assistant is not None else None,
        )

    def _research_call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from None
        except ResearchError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None

    @api.get("/research")
    def list_research() -> list[ResearchSummary]:
        return [_summary(s) for s in research.strategies()]

    @api.post("/research", status_code=status.HTTP_201_CREATED)
    def create_research(req: CreateResearch) -> ResearchDetail:
        s = _research_call(lambda: research.create(req.name))
        return _detail(s.slug)

    @api.get("/research/{slug}")
    def get_research(slug: str) -> ResearchDetail:
        return _detail(slug)

    @api.put("/research/{slug}/notes/{version}")
    def edit_note(slug: str, version: int, req: EditNote) -> ResearchDetail:
        _research_call(lambda: research.edit(slug, version, req.sections, req.implementation))
        return _detail(slug)

    @api.post("/research/{slug}/notes/{version}/freeze")
    def freeze_note(slug: str, version: int) -> ResearchDetail:
        _research_call(lambda: research.freeze(slug, version))
        return _detail(slug)

    @api.post("/research/{slug}/notes", status_code=status.HTTP_201_CREATED)
    def new_note_version(slug: str, req: Reason) -> ResearchDetail:
        _research_call(lambda: research.new_version(slug, req.reason))
        return _detail(slug)

    @api.post("/research/{slug}/chat")
    def chat_research(slug: str, req: ChatRequest) -> ResearchDetail:
        """Ask the research assistant. It sees the note and code, never any results."""
        if assistant is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "assistant not enabled")
        try:
            s = research.get(slug)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "research strategy not found") from None
        note = s.latest
        source = strategy_source(note.implementation) if note.implementation else None
        try:
            answer = assistant.reply(
                s, source, research.chat(slug), req.message, load_config(config_path)
            )
        except (AssistantError, ConfigError) as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from None
        research.append_chat(
            slug,
            [
                chat_record("user", req.message, note.version),
                chat_record("assistant", answer, note.version),
            ],
        )
        return _detail(slug)

    @api.post("/research/{slug}/reject")
    def reject_research(slug: str, req: Reason) -> ResearchDetail:
        _research_call(lambda: research.reject(slug, req.reason))
        return _detail(slug)

    # ---------- job status ----------

    @api.get("/jobs")
    def list_jobs() -> list[JobOut]:
        return [JobOut.from_job(j) for j in jobs.list()]

    @api.get("/jobs/{job_id}")
    def get_job(job_id: str) -> JobOut:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return JobOut.from_job(job)

    @api.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> JobOut:
        job = jobs.cancel(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return JobOut.from_job(job)

    app.include_router(api)

    if static_dir is not None and (static_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="dashboard")

    return app


def _strategy_out(spec: StrategySpec) -> StrategyOut:
    return StrategyOut(
        key=spec.key,
        kind=spec.kind,
        description=spec.description,
        params=[ParamOut.model_validate(asdict(p)) for p in spec.params],
        universe=spec.universe,
    )


def _series(equity: pl.DataFrame) -> Series:
    """Equity per bar close, with drawdown from the running peak (a negative fraction)."""
    frame = equity.select(
        pl.col("close_time").dt.epoch("s").alias("time"),
        "equity",
        (pl.col("equity") / pl.col("equity").cum_max() - 1).alias("drawdown"),
    )
    return Series(
        time=frame["time"].to_list(),
        equity=frame["equity"].to_list(),
        drawdown=frame["drawdown"].to_list(),
    )
