"""Performance metrics for a backtest, with their uncertainty.

Conventions (CLAUDE.md): returns are measured daily at 00:00 UTC for every strategy,
whatever its bar interval, so strategies and the benchmark are comparable; years are
365 days (crypto trades every day); the risk-free rate is 0.

Sharpe is reported with its standard error, which accounts for skew and fat tails
(Mertens 2002, as used by Bailey & Lopez de Prado's probabilistic Sharpe ratio):

    SE(SR) = sqrt((1 - skew*SR + (kurtosis - 1)/4 * SR^2) / (n - 1))

where SR, skew and kurtosis (not excess) are of daily returns. From it come a 95%
confidence interval and PSR, the probability that the true Sharpe is above zero.
These assume this was the only strategy tried; P3's deflated Sharpe corrects for
every variant tested.

Max drawdown is one path's worst case. Treat it as a floor, not a forecast.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import polars as pl

from kestrel.backtest.engine import BacktestResult

DAYS_PER_YEAR = 365
Z95 = 1.959963984540054
FEW_TRADES = 30
"""Below this many trades, per-trade statistics are too noisy to lean on."""


@dataclass(frozen=True)
class Metrics:
    start: datetime
    end: datetime
    days: int
    final_equity: float
    total_return: float
    cagr: float
    volatility: float
    sharpe: float
    sharpe_ci: tuple[float, float]
    psr: float
    """Probability the true (annualised) Sharpe is above zero."""
    sortino: float
    max_drawdown: float
    """Worst peak-to-trough fall, as a negative fraction."""
    longest_underwater: timedelta
    skew: float
    kurtosis: float
    exposure: float
    """Average fraction of equity invested."""
    trades: int
    rejections: int
    band_skips: int
    fees: float
    spread_costs: float
    impact_costs: float
    turnover: float
    """Traded notional per year as a multiple of average equity."""

    @property
    def total_costs(self) -> float:
        return self.fees + self.spread_costs + self.impact_costs

    @property
    def few_trades(self) -> bool:
        return self.trades < FEW_TRADES


def daily_equity(result: BacktestResult) -> pl.DataFrame:
    """Equity marked at each 00:00 UTC: the starting cash at the first bar's open (when
    that is midnight), then every bar close that falls on midnight."""
    eq = result.equity
    at_midnight = (eq["close_time"].dt.hour() == 0) & (eq["close_time"].dt.minute() == 0)
    marks = eq.filter(at_midnight).select("close_time", "equity")
    start = result.start
    if start is not None and start.hour == 0 and start.minute == 0:
        first = pl.DataFrame(
            {"close_time": [start], "equity": [float(result.initial_cash)]},
            schema=marks.schema,
        )
        marks = pl.concat([first, marks.filter(pl.col("close_time") > start)])
    return marks


def compute_metrics(result: BacktestResult) -> Metrics:
    daily = daily_equity(result)
    if daily.height < 3:
        raise ValueError("need at least three daily marks (two returns) for metrics")
    eq = daily["equity"]
    rets = eq.pct_change().drop_nulls()
    n = rets.len()
    start, end = daily["close_time"][0], daily["close_time"][-1]
    years = (end - start).total_seconds() / (DAYS_PER_YEAR * 86400)
    total = float(eq[-1] / eq[0] - 1)

    mean = float(rets.mean())  # type: ignore[arg-type]
    std = float(rets.std())  # type: ignore[arg-type]
    downside = float((rets.clip(upper_bound=0) ** 2).mean() ** 0.5)  # type: ignore[operator]
    skew = float(rets.skew() or 0.0)
    kurt = float(rets.kurtosis(fisher=False) or 3.0)
    ann = math.sqrt(DAYS_PER_YEAR)

    if std > 0:
        sr = mean / std
        var = (1 - skew * sr + (kurt - 1) / 4 * sr**2) / (n - 1)
        se = math.sqrt(max(var, 0.0))
        sharpe = sr * ann
        ci = ((sr - Z95 * se) * ann, (sr + Z95 * se) * ann)
        psr = _norm_cdf(sr / se) if se > 0 else float(sr > 0)
    else:
        sharpe, ci, psr = math.nan, (math.nan, math.nan), math.nan

    fills = [r.fill for r in result.fills]
    traded = sum(float(f.quantity * f.reference_price) for f in fills)
    avg_equity = float(result.equity["equity"].mean())  # type: ignore[arg-type]
    max_dd, underwater = _drawdown(result.equity)

    return Metrics(
        start=start,
        end=end,
        days=n,
        final_equity=float(eq[-1]),
        total_return=total,
        cagr=(1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else math.nan,
        volatility=std * ann,
        sharpe=sharpe,
        sharpe_ci=ci,
        psr=psr,
        sortino=mean / downside * ann if downside > 0 else math.nan,
        max_drawdown=max_dd,
        longest_underwater=underwater,
        skew=skew,
        kurtosis=kurt,
        exposure=float(result.equity["weight"].mean()),  # type: ignore[arg-type]
        trades=len(fills),
        rejections=len(result.rejections),
        band_skips=result.band_skips,
        fees=sum(float(f.fee) for f in fills),
        spread_costs=sum(float(f.spread_cost) for f in fills),
        impact_costs=sum(float(f.impact_cost) for f in fills),
        turnover=traded / avg_equity / years if years > 0 and avg_equity > 0 else math.nan,
    )


def _drawdown(equity: pl.DataFrame) -> tuple[float, timedelta]:
    """Max drawdown (negative fraction) and the longest time spent below a prior peak."""
    peak = -math.inf
    peak_time: datetime | None = None
    worst = 0.0
    longest = timedelta(0)
    for t, v in zip(equity["close_time"], equity["equity"], strict=True):
        if v >= peak:
            peak, peak_time = v, t
        else:
            worst = min(worst, v / peak - 1)
            assert peak_time is not None
            longest = max(longest, t - peak_time)
    return worst, longest


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def format_report(result: BacktestResult, m: Metrics) -> str:
    def pct(x: float) -> str:
        return "n/a" if math.isnan(x) else f"{x:+.2%}"

    def num(x: float) -> str:
        return "n/a" if math.isnan(x) else f"{x:.2f}"

    lo, hi = m.sharpe_ci
    code = result.code
    commit = code.commit if code and code.commit else "unknown"
    version = commit[:10] + (" (uncommitted changes)" if code and code.dirty else "")
    lines = [
        f"{result.strategy}  ·  {result.symbol} {result.interval.value}  ·  "
        f"{m.start:%Y-%m-%d} to {m.end:%Y-%m-%d} ({m.days} days)",
        "",
        f"  Return            {pct(m.total_return)}   (CAGR {pct(m.cagr)})",
        f"  Sharpe            {num(m.sharpe)}   95% CI [{num(lo)}, {num(hi)}]   "
        f"P(true Sharpe > 0) = {'n/a' if math.isnan(m.psr) else f'{m.psr:.0%}'}",
        f"  Max drawdown      {pct(m.max_drawdown)}   longest underwater "
        f"{m.longest_underwater.days} days",
        f"  Trades            {m.trades}"
        + ("   (too few to trust per-trade statistics)" if m.few_trades else ""),
        "",
        f"  Equity            {float(result.initial_cash):,.2f} -> {m.final_equity:,.2f} USDT",
        f"  Volatility        {'n/a' if math.isnan(m.volatility) else f'{m.volatility:.2%}'}"
        f"   Sortino {num(m.sortino)}",
        f"  Skew / kurtosis   {num(m.skew)} / {num(m.kurtosis)}",
        f"  Exposure          {m.exposure:.0%} of equity on average",
        f"  Costs             {m.total_costs:,.4f} USDT (fee {m.fees:,.4f}, spread "
        f"{m.spread_costs:,.4f}, impact {m.impact_costs:,.4f})",
        f"  Turnover          {num(m.turnover)}x equity per year",
        f"  Simulator         {m.rejections} rejected orders, {m.band_skips} rebalances "
        "skipped by the no-trade band",
        "",
        f"  Config {result.config_fingerprint}  ·  code {version}"
        + ("  ·  HOLDOUT DATA USED" if result.used_holdout else ""),
        "  Confidence intervals assume this was the only strategy tried.",
    ]
    return "\n".join(lines)
