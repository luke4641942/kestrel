"""Request and response models: the contract between the platform and any client.

These schemas are also published as OpenAPI at ``/api/docs``, so the dashboard,
scripts and future tools all read from one documented interface.
"""

from __future__ import annotations

from datetime import date, datetime
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


IndicatorLines = dict[str, dict[str, list[float | None]]]
"""Preset key -> line name -> one value per bar (None while warming up)."""


class IndicatorOut(BaseModel):
    """One entry of the dashboard's indicator menu (kestrel.indicators.PRESETS)."""

    key: str
    label: str
    pane: str
    """"price" (drawn over the candles) or "own" (its own pane underneath)."""
    description: str
    lines: list[str]
    guides: list[float]
    percent: bool


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
    indicators: IndicatorLines = Field(default_factory=dict)


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
    spread_tiers: list[tuple[float, float]] = Field(default_factory=list)
    """[universe] spread_tiers: (minimum median daily volume in USDT, half-spread in bps)."""

    @classmethod
    def from_config(cls, c: Config) -> CostConfigOut:
        return cls(
            account_currency=c.account_currency,
            fingerprint=c.fingerprint(),
            spread_tiers=[(float(v), float(b)) for v, b in c.universe.spread_tiers],
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


class ParamOut(BaseModel):
    name: str
    default: int
    low: int
    high: int
    description: str


class StrategyOut(BaseModel):
    key: str
    kind: str
    """benchmark, engine-test, research or control."""
    description: str
    params: list[ParamOut]
    universe: bool = False
    """Ranks the whole coin universe instead of trading one market."""


class SourceOut(BaseModel):
    key: str
    source: str
    commit: str | None
    dirty: bool


class SignalsRequest(BaseModel):
    symbol: str = Field(min_length=2, max_length=20)
    interval: Interval = Interval.H4
    start: str
    end: str
    params: dict[str, int] = Field(default_factory=dict)
    indicators: list[str] = Field(default_factory=list)
    """Preset keys (kestrel.indicators.PRESETS) to compute over the same bars."""

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        return _check_symbol(v)

    @field_validator("start", "end")
    @classmethod
    def _valid_month(cls, v: str) -> str:
        return _check_month(v)


class Signals(BaseModel):
    """Bars with the strategy's decision at each bar's close: a target weight, or for
    bracket strategies 1.0 on bars that signal an entry (exits depend on fills, so
    they appear in backtests, not here)."""

    kind: str = "weights"
    total_bars: int
    """Bars in the requested range. Signals are computed on all of them; only the most
    recent are returned for drawing, so ``len(time)`` can be smaller."""

    time: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    weight: list[float]
    indicators: IndicatorLines = Field(default_factory=dict)


class BacktestRequest(BaseModel):
    """Start a backtest. There is deliberately no holdout switch here: the holdout is
    for one final test of a finished strategy, run from the CLI with an explicit flag."""

    symbol: str = Field(min_length=2, max_length=20, examples=["BTCUSDT"])
    interval: Interval = Interval.H4
    strategy: str = "buy-and-hold"
    start: str = Field(description="First month, YYYY-MM")
    end: str = Field(description="Last month, YYYY-MM (inclusive)")
    initial_cash: float = Field(default=330.0, gt=0, le=1e9)
    params: dict[str, int] = Field(default_factory=dict)
    research: str | None = Field(
        default=None,
        description="Research strategy slug. Its latest note must be frozen; the run is "
        "logged as a trial and uses the implementation the note names.",
    )

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
    # Bracket strategies only (None otherwise, and for runs saved before stops existed).
    round_trips: int | None = None
    win_rate: float | None = None
    avg_win_r: float | None = None
    avg_loss_r: float | None = None
    expectancy_r: float | None = None
    profit_factor: float | None = None
    avg_cost_r: float | None = None
    risk_capped: int | None = None


class CodeOut(BaseModel):
    commit: str | None
    dirty: bool


class ResearchRef(BaseModel):
    slug: str
    version: int


class RunSummary(BaseModel):
    id: str
    created_at: datetime
    strategy: str
    # Defaults keep runs saved before these fields existed loadable.
    strategy_kind: str = "benchmark"
    style: str = "weights"
    engine: str = "event"
    params: dict[str, int] = Field(default_factory=dict)
    research: ResearchRef | None = None
    strategy_name: str
    symbol: str
    """The market, or UNIVERSE for a cross-sectional run."""
    benchmark_symbol: str | None = None
    """What the benchmark held; older runs (always one market) leave it out."""
    universe_coins: int | None = None
    """Coins ranked, for a cross-sectional run."""
    forced_exits: int = 0
    deflated_sharpe: float | None = None
    """Probability the true Sharpe beats the best of every strategy tried by luck (P3).
    None for the benchmark and for runs on the holdout."""
    deflated_trials: int | None = None
    """How many distinct configurations it was deflated against."""
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


class TradeOut(BaseModel):
    entry_time: datetime
    exit_time: datetime
    exit_reason: str
    quantity: float
    entry_price: float
    exit_price: float
    stop: float
    target: float | None
    """None when the strategy set no target."""
    risk: float
    pnl: float
    costs: float
    r_multiple: float
    cost_r: float
    risk_capped: bool


class YearOut(BaseModel):
    year: int
    strategy: float | None
    btc: float | None


class StartDateOut(BaseModel):
    window_days: int
    windows: int
    sharpe: float | None
    drawdown: float | None
    total_return: float | None


class AnalysisOut(BaseModel):
    """Deeper analysis; see kestrel.backtest.analysis. None where it doesn't apply."""

    return_ci: list[float | None]
    p_profit: float
    expectancy_ci: list[float | None] | None
    p_positive_expectancy: float | None
    min_track_record_days: float | None
    gross_profit: float
    cost_per_round_trip_bps: float | None
    break_even_cost_bps: float | None
    beta: float | None
    correlation: float | None
    matched_btc_return: float | None
    worst_day: float
    worst_month: float
    expected_shortfall: float
    years: list[YearOut]
    payoff_ratio: float | None
    profit_without_top5: float | None
    holding_buckets: dict[str, int]
    long_term_gain_share: float | None
    open_at_end: bool
    resamples: int
    block_days: int
    start_dates: list[StartDateOut] = Field(default_factory=list)
    """Against holding from every start date. Runs saved before it existed get it
    computed from their stored equity."""


class GradeOut(BaseModel):
    name: str
    rule: str
    status: str
    """pass, fail or not run."""
    measured: str


class ScorecardOut(BaseModel):
    """The run graded against the scorecard (kestrel.validation.scorecard)."""

    verdict: str
    """pass (all eight pass), fail (any fails) or incomplete (a study is missing)."""
    failed: int
    not_run: int
    grades: list[GradeOut]


class RunDetail(RunSummary):
    config: dict[str, Any]
    equity: Series
    benchmark: Series
    fills: list[FillOut]
    rejections: list[RejectionOut]
    trades: list[TradeOut] = Field(default_factory=list)
    analysis: AnalysisOut | None = None
    """Missing for runs saved before the analysis existed."""
    scorecard: ScorecardOut | None = None
    """None for buy-and-hold and holdout runs, which aren't graded."""


# ---------- research ----------


class NoteOut(BaseModel):
    version: int
    created_at: datetime
    sections: dict[str, str]
    implementation: str | None
    frozen_at: datetime | None
    change_reason: str | None


class StatusChangeOut(BaseModel):
    status: str
    at: datetime
    reason: str


class ResearchSummary(BaseModel):
    slug: str
    name: str
    status: str
    created_at: datetime
    version: int
    frozen: bool
    implementation: str | None
    runs: int
    trials: int
    """Distinct configurations tested (identical re-runs are one trial)."""
    deflated_sharpe: float | None = None
    """The best deflated Sharpe among this hypothesis's trials."""


class DecisionOut(BaseModel):
    """What to do with a tested strategy (research/decisions.toml)."""

    strategy: str
    name: str
    market: str
    decision: str
    """pursue, watch or drop."""
    decided: date
    reason: str
    evidence: list[str]


class ScorecardTestOut(BaseModel):
    name: str
    rule: str
    why: str


class LessonOut(BaseModel):
    title: str
    text: str


class RulesOut(BaseModel):
    """How strategies are judged (kestrel.validation.rules) and what the research found."""

    reviewed: date
    passing: str
    """What a pass means."""
    scorecard: list[ScorecardTestOut]
    risk_managed: str
    entry_bar: list[str]
    account: str
    lessons: list[LessonOut]
    """From research/findings.md; empty when the file is missing."""


class LookOut(BaseModel):
    params: dict[str, int]
    sharpe: float | None


class StudyOut(BaseModel):
    """A robustness check or walk-forward (kestrel.validation.log)."""

    id: str
    created_at: datetime
    kind: str
    strategy: str
    symbol: str
    interval: str
    start: str
    end: str
    looks: list[LookOut]
    verdict: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ValidationOut(BaseModel):
    """The trial log as a whole: what every result is corrected for."""

    trials: int
    """Distinct configurations tried on data before the holdout, research or not."""
    sharpe_std: float
    """Standard deviation of their annual Sharpe ratios."""
    expected_max_sharpe: float
    """Annual Sharpe the best of that many worthless strategies would reach by luck."""
    studies: list[StudyOut] = Field(default_factory=list)
    """Every validation study, newest first; their settings count as trials."""


class ChatMessage(BaseModel):
    role: str
    content: str
    at: datetime
    note_version: int | None = None


class ResearchDetail(ResearchSummary):
    sections: dict[str, str]
    """Section key -> heading, in display order."""
    notes: list[NoteOut]
    history: list[StatusChangeOut]
    trial_log: list[RunSummary]
    chat: list[ChatMessage]
    assistant_available: bool
    assistant_hint: str | None = None
    """Why the assistant may not work yet (e.g. no API key), shown before the first message."""


class CreateResearch(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class EditNote(BaseModel):
    sections: dict[str, str]
    implementation: str | None = None


class Reason(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
