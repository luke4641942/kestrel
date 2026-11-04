"""Request and response models: the contract between the platform and any client.

These schemas are also published as OpenAPI at ``/api/docs``, so the dashboard,
scripts and future tools all read from one documented interface.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from kestrel.api.jobs import Job, JobStatus
from kestrel.backtest.costs import Fill, Rejection, Side
from kestrel.config import Config
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
        return _check_symbol(v)

    @field_validator("start", "end")
    @classmethod
    def _valid_month(cls, v: str | None) -> str | None:
        return v if v is None else _check_month(v)


def _check_symbol(v: str) -> str:
    if not v.isascii() or not v.isalnum():
        raise ValueError("use the Binance pair code with no separators, e.g. BTCUSDT")
    return v.upper()


def _check_month(v: str) -> str:
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


# ---------- costs ----------
# Floats here are for display; the engine computes in exact Decimals.


class CostSettings(BaseModel):
    taker_fee_bps: float
    half_spread_bps: float
    impact_coef_bps: float
    max_participation: float


class InstrumentOut(BaseModel):
    symbol: str
    step_size: float
    min_qty: float
    min_notional: float


class BacktestSettings(BaseModel):
    no_trade_band: float
    holdout_start: datetime


class CostConfigOut(BaseModel):
    account_currency: str
    fingerprint: str
    costs: CostSettings
    backtest: BacktestSettings
    instruments: list[InstrumentOut]

    @classmethod
    def from_config(cls, c: Config) -> CostConfigOut:
        return cls(
            account_currency=c.account_currency,
            fingerprint=c.fingerprint(),
            costs=CostSettings(
                taker_fee_bps=float(c.costs.taker_fee_bps),
                half_spread_bps=float(c.costs.half_spread_bps),
                impact_coef_bps=float(c.costs.impact_coef_bps),
                max_participation=float(c.costs.max_participation),
            ),
            backtest=BacktestSettings(
                no_trade_band=float(c.backtest.no_trade_band),
                holdout_start=c.backtest.holdout_start,
            ),
            instruments=[
                InstrumentOut(
                    symbol=r.symbol,
                    step_size=float(r.step_size),
                    min_qty=float(r.min_qty),
                    min_notional=float(r.min_notional),
                )
                for r in c.instruments.values()
            ],
        )


class Quote(BaseModel):
    """One simulated market order. ``filled`` is false when the simulator rejects it."""

    symbol: str
    side: Side
    filled: bool
    reason: str | None = None
    reference_price: float
    bar_volume: float
    quantity: float | None = None
    price: float | None = None
    notional: float | None = None
    participation: float | None = None
    fee: float | None = None
    spread_cost: float | None = None
    impact_cost: float | None = None
    total_cost: float | None = None
    cash_flow: float | None = None

    @classmethod
    def from_result(cls, result: Fill | Rejection, price: Decimal, volume: Decimal) -> Quote:
        base: dict[str, Any] = {
            "symbol": result.symbol,
            "side": result.side,
            "reference_price": float(price),
            "bar_volume": float(volume),
        }
        if isinstance(result, Rejection):
            return cls(**base, filled=False, reason=result.reason)
        notional = result.quantity * result.reference_price
        return cls(
            **base,
            filled=True,
            quantity=float(result.quantity),
            price=float(result.price),
            notional=float(notional),
            participation=float(notional / volume),
            fee=float(result.fee),
            spread_cost=float(result.spread_cost),
            impact_cost=float(result.impact_cost),
            total_cost=float(result.total_cost),
            cash_flow=float(result.cash_flow),
        )


class CurvePoint(BaseModel):
    """Cost of one order, in bps of its notional at the reference price."""

    notional: float
    fee_bps: float
    spread_bps: float
    impact_bps: float
    total_bps: float


class CostCurve(BaseModel):
    symbol: str
    side: Side
    reference_price: float
    bar_volume: float
    min_notional: float
    """Smallest order the exchange accepts."""
    max_notional: float
    """Largest order the simulator fills (max participation of the bar's volume)."""
    points: list[CurvePoint]


# ---------- backtests ----------


class StrategyOut(BaseModel):
    key: str
    description: str


class BacktestRequest(BaseModel):
    """Start a backtest. There is deliberately no holdout switch here: the holdout is
    for one final test of a finished strategy, run from the CLI with an explicit flag."""

    symbol: str = Field(min_length=2, max_length=20, examples=["BTCUSDT"])
    interval: Interval = Interval.H4
    strategy: str = "buy-and-hold"
    start: str = Field(description="First month, YYYY-MM")
    end: str = Field(description="Last month, YYYY-MM (inclusive)")
    initial_cash: float = Field(default=330.0, gt=0, le=1e9)

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        return _check_symbol(v)

    @field_validator("start", "end")
    @classmethod
    def _valid_month(cls, v: str) -> str:
        return _check_month(v)


class MetricsOut(BaseModel):
    """Backtest metrics. None where a value is undefined (e.g. Sharpe of flat equity)."""

    start: datetime
    end: datetime
    days: int
    final_equity: float
    total_return: float
    cagr: float | None
    volatility: float | None
    sharpe: float | None
    sharpe_ci: list[float | None]
    psr: float | None
    sortino: float | None
    max_drawdown: float
    longest_underwater_days: float
    skew: float | None
    kurtosis: float | None
    exposure: float
    trades: int
    rejections: int
    band_skips: int
    fees: float
    spread_costs: float
    impact_costs: float
    total_costs: float
    turnover: float | None
    few_trades: bool


class CodeOut(BaseModel):
    commit: str | None
    dirty: bool


class RunSummary(BaseModel):
    id: str
    created_at: datetime
    strategy: str
    strategy_name: str
    symbol: str
    interval: Interval
    start: str
    end: str
    initial_cash: str
    account_currency: str
    used_holdout: bool
    config_fingerprint: str
    code: CodeOut | None
    metrics: MetricsOut
    benchmark_metrics: MetricsOut


class Series(BaseModel):
    """Column-oriented time series; ``time`` is epoch seconds (UTC)."""

    time: list[int]
    equity: list[float]
    drawdown: list[float]


class FillOut(BaseModel):
    time: datetime
    side: str
    quantity: float
    reference_price: float
    price: float
    fee: float
    spread_cost: float
    impact_cost: float
    cash_flow: float


class RejectionOut(BaseModel):
    time: datetime
    side: str
    quantity: float
    reason: str


class RunDetail(RunSummary):
    config: dict[str, Any]
    equity: Series
    benchmark: Series
    fills: list[FillOut]
    rejections: list[RejectionOut]
