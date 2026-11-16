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
from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, FastAPI, HTTPException, Query, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
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
    CostGateOut,
    CreateResearch,
    CurvePoint,
    Dataset,
    DecisionOut,
    EditNote,
    FillOut,
    GradeOut,
    Health,
    HoldoutOut,
    IndicatorLines,
    IndicatorOut,
    IngestRequest,
    JobOut,
    LessonOut,
    LuckPointOut,
    NoteOut,
    PaperDecisionOut,
    PaperDetailOut,
    PaperEventOut,
    PaperFillOut,
    PaperSummaryOut,
    PaperTrackingOut,
    ParamOut,
    QualityOut,
    Quote,
    Reason,
    RegimeDetailOut,
    RegimeRequest,
    RegimeSummaryOut,
    RejectionOut,
    ResearchDetail,
    ResearchSummary,
    RulesOut,
    RunDetail,
    RunSummary,
    ScorecardOut,
    ScorecardTestOut,
    Series,
    ShortCurveOut,
    ShortStudyOut,
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
from kestrel.backtest.analysis import start_dates
from kestrel.backtest.costs import CostModel, Fill, Side
from kestrel.backtest.engine import HoldoutError
from kestrel.backtest.metrics import DAYS_PER_YEAR
from kestrel.backtest.runs import RunRequest, RunStore, count_trials, execute
from kestrel.config import Config, ConfigError, InstrumentRules, load_config
from kestrel.data.binance_vision import BinanceVisionClient, Month, last_complete_month
from kestrel.data.features import attach_features
from kestrel.data.quality import check_klines
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.decisions import DecisionError, load_decisions
from kestrel.execution import paper, safety, tracking
from kestrel.indicators import PRESETS, compute_presets
from kestrel.provenance import code_version
from kestrel.regimes import RegimeStore
from kestrel.regimes import walk_forward as walk_regimes
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
    spec_for,
    strategy_source,
    validated_brackets,
    validated_weights,
)
from kestrel.studies import cost_gate, shorting
from kestrel.validation import rules
from kestrel.validation.deflated import (
    TrialSet,
    counts_as_trial,
    deflated_sharpe,
    pool_of,
    trial_history,
    trial_set,
)
from kestrel.validation.holdout import HoldoutLog
from kestrel.validation.log import ValidationLog
from kestrel.validation.scorecard import grade

STATIC_DIR = Path(__file__).parent / "static"
MAX_BARS = 20_000

ClientFactory = Callable[[], BinanceVisionClient]


LOCAL_HOSTS = ("127.0.0.1", "localhost", "testserver")
"""Host headers the API answers: this machine's names, plus Starlette's test client."""


def create_app(
    data_dir: Path | str = "data",
    client_factory: ClientFactory = BinanceVisionClient,
    static_dir: Path | None = STATIC_DIR,
    config_path: Path | str = "kestrel.toml",
    research_dir: Path | str = "research",
    assistant: ResearchAssistant | None = None,
    allowed_hosts: tuple[str, ...] = LOCAL_HOSTS,
) -> FastAPI:
    store = ParquetStore(data_dir)
    runs = RunStore(data_dir)
    studies = ValidationLog(data_dir)
    research = ResearchStore(research_dir)
    regime_store = RegimeStore(data_dir)
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
    # Only answer requests addressed to this machine by name. Without this, a web page could
    # point its own domain at 127.0.0.1 (DNS rebinding) and drive the API from the browser.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
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

    @api.get("/indicators")
    def list_indicators() -> list[IndicatorOut]:
        return [
            IndicatorOut(
                key=p.key,
                label=p.label,
                pane=p.pane,
                description=p.description,
                lines=list(p.lines),
                guides=list(p.guides),
                percent=p.percent,
                reading=p.reading,
            )
            for p in PRESETS.values()
        ]

    def _indicator_lines(bars: pl.DataFrame, keys: list[str], shown: int) -> IndicatorLines:
        """Presets computed over all of ``bars``; the last ``shown`` values of each line,
        with NaN and infinities (0/0 volume, say) as None so they serialise."""
        if not keys:
            return {}
        try:
            computed = compute_presets(bars, keys)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

        def clean(line: pl.Series) -> list[float | None]:
            return [
                v if v is not None and math.isfinite(v) else None
                for v in line.tail(shown).to_list()
            ]

        return {
            k: {name: clean(line) for name, line in lines.items()} for k, lines in computed.items()
        }

    @api.get("/datasets/{symbol}/{interval}/bars")
    def get_bars(
        symbol: str,
        interval: Interval,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = Query(default=2_000, ge=1, le=MAX_BARS),
        indicators: Annotated[list[str] | None, Query()] = None,
    ) -> Bars:
        """Bars in ``[start, end)``; without a start, the most recent ``limit`` bars. Any
        ``indicators`` are computed over the stored history before the first bar shown,
        so a long average isn't blank at the chart's left edge."""
        symbol = _require(symbol, interval)
        indicators = indicators or []
        history = store.read(symbol, interval, None, end) if indicators else None
        df = store.read(symbol, interval, start, end)
        df = df.head(limit) if start is not None else df.tail(limit)
        lines: IndicatorLines = {}
        if history is not None and df.height:
            upto = history.filter(pl.col("open_time") <= df["open_time"].max())
            lines = _indicator_lines(upto, indicators, df.height)
        return Bars(
            indicators=lines,
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

    @api.get("/decisions")
    def get_decisions() -> list[DecisionOut]:
        """What to do with each tested strategy: pursue, watch or drop."""
        try:
            found = load_decisions(Path(research_dir) / "decisions.toml")
        except DecisionError as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from None
        return [DecisionOut.model_validate(asdict(d)) for d in found]

    @api.get("/rules")
    def get_rules() -> RulesOut:
        """The research rules every strategy is judged by, and the lessons so far."""
        path = Path(research_dir) / "findings.md"
        try:
            found = rules.lessons(path.read_text()) if path.exists() else []
        except ValueError as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from None
        return RulesOut(
            reviewed=rules.REVIEWED,
            passing=rules.PASS,
            scorecard=[ScorecardTestOut.model_validate(asdict(t)) for t in rules.SCORECARD],
            risk_managed=rules.RISK_MANAGED,
            entry_bar=list(rules.ENTRY_BAR),
            account=rules.ACCOUNT,
            lessons=[LessonOut.model_validate(asdict(x)) for x in found],
        )

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
            bars = attach_features(bars, spec_for(key).features, req.symbol, req.interval, data_dir)
            if isinstance(strategy, BracketStrategy):
                kind = "entries"
                weights = validated_brackets(strategy, bars)["enter"].cast(pl.Float64)
            else:
                kind, weights = "weights", validated_weights(strategy, bars)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        # Computed on the whole range, exactly as a backtest would; only drawing is capped.
        total, full = bars.height, bars
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
            indicators=_indicator_lines(full, req.indicators, bars.height),
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

    @api.get("/cost-gate")
    def list_cost_gate() -> list[CostGateOut]:
        """The saved cost gates, shortest bars first. Read-only: run with kestrel cost-gate."""
        return [CostGateOut.model_validate(d) for d in cost_gate.load_all(data_dir)]

    @api.get("/short-studies")
    def list_short_studies() -> list[ShortStudyOut]:
        """The hypothetical shorting studies, passes first. Read-only: each runs once from
        its frozen note with kestrel short-study."""
        out = []
        for d in shorting.load_all(data_dir):
            curves = [
                ShortCurveOut(
                    label=c["label"],
                    series=_series(
                        pl.DataFrame(
                            {
                                "close_time": [
                                    datetime.fromisoformat(t).replace(tzinfo=UTC) for t in c["time"]
                                ],
                                "equity": c["equity"],
                            }
                        )
                    ),
                )
                for c in d["curves"]
            ]
            out.append(ShortStudyOut.model_validate({**d, "curves": curves}))
        return out

    @api.get("/holdout")
    def list_holdout() -> list[HoldoutOut]:
        """Every holdout test ever recorded, newest first. Read-only: holdout tests run only
        from the CLI, once per frozen note."""
        found = HoldoutLog(data_dir).records()
        found.sort(key=lambda r: r["recorded_at"], reverse=True)
        return [HoldoutOut.model_validate(r) for r in found]

    # ---------- paper trading (read-only) ----------

    def _paper_summary(account: paper.PaperAccount) -> PaperSummaryOut:
        terms, state = account.terms(), account.state()
        marks = [e for e in account.ledger.events() if e["type"] == "mark"]
        return PaperSummaryOut(
            name=terms.name,
            strategy=terms.strategy,
            symbol=terms.symbol,
            interval=terms.interval.value,
            venue=terms.venue,
            research=terms.research,
            initial_cash=float(terms.initial_cash),
            trade_from=terms.trade_from,
            last_bar=state.last_bar,
            equity=float(marks[-1]["equity"]) if marks else None,
            cash=float(state.cash),
            position=float(state.position),
            positions={k: float(v) for k, v in state.positions.items() if v > 0},
            halted=safety.halted(account),
        )

    def _decision_out(e: dict[str, Any]) -> PaperDecisionOut:
        if "targets" in e:  # a basket
            targets = {k: None if w is None else float(w) for k, w in (e["targets"] or {}).items()}
            return PaperDecisionOut(
                bar=datetime.fromisoformat(e["bar"]),
                target=sum(w or 0.0 for w in targets.values()),
                current=None,
                missed=bool(e.get("missed")),
                band_skip=False,
                targets=targets,
            )
        return PaperDecisionOut(
            bar=datetime.fromisoformat(e["bar"]),
            target=float(e["target"]),
            current=float(e["current"]),
            missed=bool(e.get("missed")),
            band_skip=bool(e.get("band_skip")),
        )

    @api.get("/paper")
    def list_paper() -> list[PaperSummaryOut]:
        """Every paper account. Accounts are opened and run from the CLI only."""
        return [_paper_summary(paper.PaperAccount(data_dir, n)) for n in paper.accounts(data_dir)]

    @api.get("/paper/{name}")
    def get_paper(name: str) -> PaperDetailOut:
        if name not in paper.accounts(data_dir):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such paper account")
        account = paper.PaperAccount(data_dir, name)
        terms = account.terms()
        spec = spec_for(terms.strategy)
        if spec.universe:
            panel = paper.basket_panel(store, terms, list(spec.assets), datetime.now(UTC))
            tracked = tracking.track_basket(account, panel, load_config(config_path))
        else:
            bars = attach_features(
                store.read(terms.symbol, terms.interval),
                spec.features,
                terms.symbol,
                terms.interval,
                data_dir,
            )
            tracked = tracking.track(account, bars, load_config(config_path))
        step = terms.interval.duration

        def series(times: list[datetime], values: list[float]) -> Series:
            frame = pl.DataFrame({"close_time": [t + step for t in times], "equity": values})
            return _series(frame) if frame.height else Series(time=[], equity=[], drawdown=[])

        days = tracked.days
        events = account.ledger.events()
        return PaperDetailOut(
            **_paper_summary(account).model_dump(),
            equity_series=series([d.bar for d in days], [float(d.equity) for d in days]),
            expected=series([d.bar for d in days], [float(d.expected) for d in days]),
            fills=[
                PaperFillOut(
                    at=datetime.fromisoformat(f.at),
                    symbol=f.symbol,
                    side=f.side,
                    quantity=float(e["quantity"]),
                    reference_price=float(e["reference_price"]),
                    price=float(e["price"]),
                    fee=float(e["fee"]),
                    cost_bps=float(f.cost_bps),
                    expected_bps=None if f.expected_bps is None else float(f.expected_bps),
                )
                for f, e in zip(
                    tracked.fills, [e for e in events if e["type"] == "fill"], strict=True
                )
            ],
            decisions=[
                _decision_out(e)
                for e in reversed([e for e in events if e["type"] == "decision"])
                # A basket decides weekly: its other days carry no decision.
                if "targets" not in e or e["targets"] is not None or e.get("missed")
            ],
            events=[
                PaperEventOut(
                    type=e["type"],
                    at=datetime.fromisoformat(e["at"]),
                    reason=e.get("reason"),
                )
                for e in events
                if e["type"] in ("halt", "resume", "kill")
            ],
            tracking=PaperTrackingOut(
                days=len(days),
                decision_mismatches=tracked.decision_mismatches,
                missed=tracked.missed,
                difference=None if tracked.difference is None else float(tracked.difference),
                cost_bps=_float(tracked.mean_cost()),
                expected_bps=_float(tracked.mean_cost(expected=True)),
            ),
        )

    # ---------- regimes ----------

    @api.get("/regimes")
    def list_regimes() -> list[RegimeSummaryOut]:
        return [RegimeSummaryOut.model_validate(d) for d in regime_store.summaries()]

    @api.get("/regimes/{symbol}")
    def get_regimes(symbol: str) -> RegimeDetailOut:
        try:
            doc = regime_store.load(symbol.upper())
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no regimes for this market") from None
        return RegimeDetailOut.model_validate({**doc, "refits": len(doc["refits"])})

    @api.post("/jobs/regimes", status_code=status.HTTP_202_ACCEPTED)
    def start_regimes(req: RegimeRequest) -> JobOut:
        symbol = req.symbol.upper()

        def run(ctx: JobContext) -> dict[str, Any]:
            try:
                holdout = load_config(config_path).backtest.holdout_start
            except ConfigError as e:
                raise JobError(str(e)) from None
            # Everything before the holdout, never the holdout itself.
            bars = store.read(symbol, Interval.D1, datetime(2000, 1, 1, tzinfo=UTC), holdout)
            if bars.height == 0:
                raise JobError(f"no daily bars for {symbol}; ingest {symbol} 1d first")
            ctx.log(f"Fitting regimes on {symbol} 1d, {bars.height} days before the holdout")

            def month(done: int, total: int) -> None:
                ctx.check_cancelled()
                ctx.progress(0.95 * done / max(total, 1), f"Month {done + 1} of {total}")

            try:
                result = walk_regimes(bars, on_month=month)
            except ValueError as e:
                raise JobError(str(e)) from None
            if not result.refits:
                raise JobError(f"{symbol} has too little daily history for a first fit (4 years)")
            ctx.progress(0.97, "Saving")
            regime_store.save(symbol, result, bars)
            ctx.log(f"Saved {len(result.refits)} monthly fits")
            return {"symbol": symbol}

        return JobOut.from_job(jobs.submit("regimes", {"symbol": symbol}, run))

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

    @api.get("/validation/history")
    def get_validation_history() -> list[LuckPointOut]:
        """How the luck bar moved: the trial log after each run or study that logged one."""
        return [
            LuckPointOut(at=d, trials=t.count, luck_bar=t.expected_max_sharpe_annual)
            for d, t in trial_history(runs.summaries(), studies.studies())
        ]

    def _deflated(metas: list[dict[str, Any]], trials: TrialSet | None = None) -> list[RunSummary]:
        """Run summaries with each one's Sharpe deflated for every strategy tried in its
        pool (confirmation runs of the exploration experiment are counted apart)."""
        main = trials or trial_set(runs.summaries(), studies.studies())
        confirming: TrialSet | None = None
        out = []
        for meta in metas:
            m = meta["metrics"]
            t = main
            if pool_of(meta) == "confirmation":
                if confirming is None:
                    confirming = trial_set(runs.summaries(), studies.studies(), "confirmation")
                t = confirming
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
        analysis = saved.meta.get("analysis")
        if analysis is not None and "start_dates" not in analysis:
            rows = start_dates(saved.equity, saved.benchmark)
            analysis = {**analysis, "start_dates": [asdict(r) for r in rows]}
        card = grade(saved.meta, summary.deflated_sharpe, studies.studies(), analysis)
        return RunDetail.model_validate(
            {
                **saved.meta,
                "analysis": analysis,
                "deflated_sharpe": summary.deflated_sharpe,
                "deflated_trials": summary.deflated_trials,
                "scorecard": None
                if card is None
                else ScorecardOut(
                    verdict=card.verdict,
                    failed=card.failed,
                    not_run=card.not_run,
                    grades=[GradeOut.model_validate(asdict(g)) for g in card.grades],
                ),
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
        source = strategy_source(note.implementation) if note.implementation in STRATEGIES else None
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


def _float(x: Decimal | None) -> float | None:
    return None if x is None else float(x)


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
