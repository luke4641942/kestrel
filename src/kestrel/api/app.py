"""HTTP API over the platform, plus the built dashboard as static files.

The engine never imports anything from here: the API is a thin adapter that
calls platform functions, so the same operations stay available from the CLI,
scripts and tests without a web server.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import APIRouter, FastAPI, HTTPException, Query, status
from fastapi.staticfiles import StaticFiles

from kestrel import __version__
from kestrel.api.jobs import JobContext, JobError, JobManager
from kestrel.api.models import Bars, Dataset, Health, IngestRequest, JobOut, QualityOut
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
