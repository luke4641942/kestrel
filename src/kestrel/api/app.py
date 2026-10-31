"""HTTP API over the platform, plus the built dashboard as static files.

The engine never imports anything from here: the API is a thin adapter that
calls platform functions, so the same operations stay available from the CLI,
scripts and tests without a web server.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
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
    Bars,
    CostConfigOut,
    CostCurve,
    CurvePoint,
    Dataset,
    Health,
    IngestRequest,
    JobOut,
    QualityOut,
    Quote,
)
from kestrel.backtest.costs import CostModel, Fill, Side
from kestrel.config import Config, ConfigError, InstrumentRules, load_config
from kestrel.data.binance_vision import BinanceVisionClient, Month, last_complete_month
from kestrel.data.quality import check_klines
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore

STATIC_DIR = Path(__file__).parent / "static"
MAX_BARS = 20_000

ClientFactory = Callable[[], BinanceVisionClient]


def create_app(
    data_dir: Path | str = "data",
    client_factory: ClientFactory = BinanceVisionClient,
    static_dir: Path | None = STATIC_DIR,
    config_path: Path | str = "kestrel.toml",
) -> FastAPI:
    store = ParquetStore(data_dir)
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
            if result.frames:
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
