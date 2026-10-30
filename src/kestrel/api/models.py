"""Request and response models: the contract between the platform and any client.

These schemas are also published as OpenAPI at ``/api/docs``, so the dashboard,
scripts and future tools all read from one documented interface.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from kestrel.api.jobs import Job, JobStatus
from kestrel.data.binance_vision import Month
from kestrel.data.quality import QualityReport
from kestrel.data.schema import Interval


class Health(BaseModel):
    status: str
    version: str
    data_dir: str


class Dataset(BaseModel):
    venue: str
    symbol: str
    interval: Interval
    months: int
    rows: int
    first: datetime | None
    last: datetime | None


class Bars(BaseModel):
    """Column-oriented OHLCV. ``time`` is epoch seconds (UTC), as charting libraries expect."""

    symbol: str
    interval: Interval
    time: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]


class GapOut(BaseModel):
    after: datetime
    before: datetime
    missing_bars: int


class QualityOut(BaseModel):
    symbol: str
    interval: Interval
    is_clean: bool
    rows: int
    first: datetime | None
    last: datetime | None
    duplicates: int
    unsorted: bool
    misaligned: int
    ohlc_violations: int
    non_positive_prices: int
    negative_volumes: int
    nulls: int
    missing_bars: int
    gaps: list[GapOut]

    @classmethod
    def from_report(cls, symbol: str, interval: Interval, r: QualityReport) -> QualityOut:
        return cls(
            symbol=symbol,
            interval=interval,
            is_clean=r.is_clean,
            rows=r.rows,
            first=r.first,
            last=r.last,
            duplicates=r.duplicates,
            unsorted=r.unsorted,
            misaligned=r.misaligned,
            ohlc_violations=r.ohlc_violations,
            non_positive_prices=r.non_positive_prices,
            negative_volumes=r.negative_volumes,
            nulls=r.nulls,
            missing_bars=r.missing_bars,
            gaps=[
                GapOut(after=g.after, before=g.before, missing_bars=g.missing_bars) for g in r.gaps
            ],
        )


class IngestRequest(BaseModel):
    symbol: str = Field(min_length=2, max_length=20, examples=["BTCUSDT"])
    interval: Interval = Interval.H1
    start: str = Field(description="First month, YYYY-MM")
    end: str | None = Field(default=None, description="Last month, YYYY-MM; default last complete")

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        if not v.isascii() or not v.isalnum():
            raise ValueError("use the Binance pair code with no separators, e.g. BTCUSDT")
        return v.upper()

    @field_validator("start", "end")
    @classmethod
    def _valid_month(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                Month.parse(v)
            except (ValueError, TypeError) as exc:
                raise ValueError("must be YYYY-MM") from exc
        return v


class JobOut(BaseModel):
    id: str
    kind: str
    params: dict[str, Any]
    status: JobStatus
    progress: float
    message: str
    logs: list[str]
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_job(cls, job: Job) -> JobOut:
        return cls(
            id=job.id,
            kind=job.kind,
            params=job.params,
            status=job.status,
            progress=job.progress,
            message=job.message,
            logs=list(job.logs),
            result=job.result,
            error=job.error,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
