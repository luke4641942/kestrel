"""Forex runs (P6): their metrics, and where they are kept.

**Metrics on calendar days.** Forex trades five days a week; every other Kestrel result is
measured on calendar days and annualised over 365. So a forex run's equity is marked on
every calendar day at its latest close (unchanged over weekends and holidays, when
nothing is marked), and the formulas are the crypto engine's (kestrel.backtest.metrics).
A Sharpe here then means what it means everywhere else, and the deflated Sharpe applies
unchanged. A trading day is dated by its New York close (Monday's day closes Monday
17:00 New York).

**Kept apart.** Runs are saved under ``<data>/fx/runs/<id>/`` (``run.json`` and
``equity.parquet``), never with the crypto runs, so nothing that reads those (the
dashboard, the overlap report, scorecards) sees a forex run. Each summary says
``market: "forex"``: its trials count in the forex pool only
(kestrel.validation.deflated.pool_of).
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from kestrel.atomic import write_atomically
from kestrel.backtest.fx_engine import FxResult
from kestrel.backtest.metrics import DAYS_PER_YEAR, Z95, _norm_cdf
from kestrel.data.fx import NEW_YORK

MARKET = "forex"


def calendar_equity(result: FxResult) -> pl.DataFrame:
    """Equity on every calendar day (New York close dates), from the starting equity the
    day before the first close to the last close, carried over days with no close."""
    eq = result.equity
    if eq.is_empty():
        return pl.DataFrame(schema={"date": pl.Date, "equity": pl.Float64})
    closes = eq.select(
        pl.col("close_time").dt.convert_time_zone(NEW_YORK).dt.date().alias("date"), "equity"
    )
    by_day = dict(zip(closes["date"].to_list(), closes["equity"].to_list(), strict=True))
    first: date = closes["date"][0]
    last: date = closes["date"][-1]
    start = float(result.initial_equity)
    days, values = [first - timedelta(days=1)], [start]
    d, value = first, start
    while d <= last:
        value = by_day.get(d, value)
        days.append(d)
        values.append(value)
        d += timedelta(days=1)
    return pl.DataFrame(
        {"date": days, "equity": values}, schema={"date": pl.Date, "equity": pl.Float64}
    )


@dataclass(frozen=True)
class FxMetrics:
    start: date
    end: date
    days: int
    """Daily returns measured (calendar days)."""
    final_equity: float
    total_return: float
    cagr: float
    volatility: float
    sharpe: float
    sharpe_ci: tuple[float, float]
    psr: float
    max_drawdown: float
    longest_underwater_days: int
    skew: float
    kurtosis: float
    average_gross: float
    """Average sum of |weights| held: leverage used."""
    average_net: float
    """Average signed exposure: positive is net long the base currencies."""
    trades: int
    band_skips: int
    spread_costs: float
    financing: float
    """Net, in account currency: positive means it earned more than it paid."""
    turnover: float
    """Traded notional per year as a multiple of average equity."""
    wiped_out: bool
    cost_per_markup_pct: float = 0.0
    """Extra financing, in account currency, for each 1% a year more markup (about; from
    the notional held each night). The note's 2.5% check reads it."""
    spread_break_even: float = math.nan
    """How many times the spread paid it could have paid before the profit was gone."""
    windows: int = 0
    """Non-overlapping 2-year windows from the start (a partial last one left out)."""
    windows_positive: int = 0
    """Of those, how many had a Sharpe above zero."""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start"], d["end"] = self.start.isoformat(), self.end.isoformat()
        return d


def compute_fx_metrics(result: FxResult) -> FxMetrics:
    daily = calendar_equity(result)
    if daily.height < 3:
        raise ValueError("need at least three daily marks (two returns) for metrics")
    eq = daily["equity"]
    rets = eq.pct_change().drop_nulls()
    n = rets.len()
    years = (daily["date"][-1] - daily["date"][0]).days / DAYS_PER_YEAR
    total = float(eq[-1] / eq[0] - 1)
    mean, std = float(rets.mean()), float(rets.std())  # type: ignore[arg-type]
    skew = float(rets.skew() or 0.0)
    kurt = float(rets.kurtosis(fisher=False) or 3.0)
    ann = math.sqrt(DAYS_PER_YEAR)
    if std > 0:
        sr = mean / std
        se = math.sqrt(max((1 - skew * sr + (kurt - 1) / 4 * sr**2) / (n - 1), 0.0))
        sharpe = sr * ann
        ci = ((sr - Z95 * se) * ann, (sr + Z95 * se) * ann)
        psr = _norm_cdf(sr / se) if se > 0 else float(sr > 0)
    else:
        sharpe, ci, psr = math.nan, (math.nan, math.nan), math.nan

    peak, worst, peak_day, longest = -math.inf, 0.0, daily["date"][0], 0
    for d, v in zip(daily["date"], eq, strict=True):
        if v >= peak:
            peak, peak_day = v, d
        else:
            worst = min(worst, v / peak - 1)
            longest = max(longest, (d - peak_day).days)
    avg_equity = float(result.equity["equity"].mean())  # type: ignore[arg-type]
    traded = sum(f.notional for f in result.fills)
    profit = float(eq[-1]) - float(eq[0])
    spread = result.spread_cost
    windows = positive = 0
    span = 730
    for k in range((daily.height - 1) // span):
        part = eq.slice(k * span, span + 1).pct_change().drop_nulls()
        sd = float(part.std() or 0.0)  # type: ignore[arg-type]
        windows += 1
        positive += int(sd > 0 and float(part.mean()) / sd > 0)  # type: ignore[arg-type]
    return FxMetrics(
        start=daily["date"][0],
        end=daily["date"][-1],
        days=n,
        final_equity=float(eq[-1]),
        total_return=total,
        cagr=(1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else math.nan,
        volatility=std * ann,
        sharpe=sharpe,
        sharpe_ci=ci,
        psr=psr,
        max_drawdown=worst,
        longest_underwater_days=longest,
        skew=skew,
        kurtosis=kurt,
        average_gross=float(result.equity["gross"].mean()),  # type: ignore[arg-type]
        average_net=float(result.equity["net"].mean()),  # type: ignore[arg-type]
        trades=len(result.fills),
        band_skips=result.band_skips,
        spread_costs=result.spread_cost,
        financing=result.financing,
        turnover=traded / avg_equity / years if years > 0 and avg_equity > 0 else math.nan,
        wiped_out=result.wiped_out is not None,
        cost_per_markup_pct=result.notional_nights / 365 / 100,
        spread_break_even=(profit + spread) / spread if spread > 0 else math.nan,
        windows=windows,
        windows_positive=positive,
    )


def format_fx_report(result: FxResult, m: FxMetrics, extra: list[str] | None = None) -> str:
    def pct(x: float) -> str:
        return f"{x:+.2%}"

    lines = [
        f"{result.strategy}  ·  {', '.join(result.pairs)}  ·  {m.start} to {m.end} ({m.days} days)",
        "",
        f"  Return            {pct(m.total_return)}   (CAGR {pct(m.cagr)})",
        f"  Sharpe            {m.sharpe:.2f}   95% CI [{m.sharpe_ci[0]:.2f}, "
        f"{m.sharpe_ci[1]:.2f}]   P(true Sharpe > 0) = {m.psr:.0%}",
        f"  Max drawdown      {m.max_drawdown:.2%}   longest underwater "
        f"{m.longest_underwater_days} days",
        f"  Trades            {m.trades}   ({m.band_skips} skipped by the no-trade band)",
        "",
        f"  Equity            {result.initial_equity:,.2f} -> {m.final_equity:,.2f} "
        f"{result.account_currency}",
        f"  Volatility        {m.volatility:.2%}   skew {m.skew:.2f}, kurtosis {m.kurtosis:.2f}",
        f"  Exposure          gross {m.average_gross:.2f}x, net {m.average_net:+.2f}x on average",
        f"  Costs             spread {m.spread_costs:,.2f}, financing {m.financing:+,.2f} "
        f"{result.account_currency} (+ means earned)",
        f"  Turnover          {m.turnover:.1f}x equity per year",
        f"  Carry             financing was {m.financing:+,.2f} of a "
        f"{m.final_equity - result.initial_equity:+,.2f} profit; each +1%/yr of markup costs "
        f"about {m.cost_per_markup_pct:,.2f}",
        f"  Break-even        the spread could be {m.spread_break_even:.1f}x what it was before "
        "the profit is gone",
        f"  2-year windows    Sharpe above zero in {m.windows_positive} of {m.windows}",
    ]
    if m.wiped_out:
        lines.append(f"  WIPED OUT on {result.wiped_out:%Y-%m-%d}: the account was lost")
    lines += extra or []
    lines.append("  Confidence intervals assume this was the only strategy tried.")
    return "\n".join(lines)


class FxRunStore:
    """Saved forex runs: ``<data>/fx/runs/<id>/run.json`` and ``equity.parquet``."""

    def __init__(self, data_dir: Path | str) -> None:
        self.root = Path(data_dir) / "fx" / "runs"

    def save(
        self,
        result: FxResult,
        metrics: FxMetrics,
        *,
        params: dict[str, int],
        start: str,
        end: str,
        research: dict[str, Any] | None,
        detail: dict[str, Any] | None = None,
    ) -> str:
        created = datetime.now(UTC)
        run_id = f"{created:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        target = self.root / run_id
        target.mkdir(parents=True)
        meta = {
            "id": run_id,
            "created_at": created.isoformat(),
            "market": MARKET,
            "strategy": result.strategy,
            "params": params,
            "symbol": ",".join(result.pairs),
            "interval": "1d",
            "start": start,
            "end": end,
            "initial_cash": result.initial_equity,
            "account_currency": result.account_currency,
            "research": research,
            "used_holdout": result.used_holdout,
            "config_fingerprint": result.config_fingerprint,
            "code": asdict(result.code) if result.code else None,
            "metrics": metrics.to_dict(),
            "detail": detail or {},
        }
        result.equity.write_parquet(target / "equity.parquet")
        text = json.dumps(meta, indent=2, default=str)
        write_atomically(target / "run.json", lambda tmp: tmp.write_text(text))
        return run_id

    def summaries(self) -> list[dict[str, Any]]:
        """Every saved forex run's metadata, newest first."""
        found = [json.loads(p.read_text()) for p in self.root.glob("*/run.json")]
        return sorted(found, key=lambda r: r["created_at"], reverse=True)
