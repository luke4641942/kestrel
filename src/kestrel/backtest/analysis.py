"""Deeper analysis of a backtest against its benchmark: is the edge real, where does the
money come from, and what would it be like to live with?

Confidence comes from a circular block bootstrap of daily returns: whole blocks of
consecutive days are resampled (block length about n^(1/3) days), which keeps crypto's
streaks and volatility clustering that resampling single days would destroy. Block sums
come from prefix sums of log returns, so 2,000 resamples cost about 2,000 x n/block
additions. The seed is fixed, so a run always reports the same intervals.

Caveat shown with every interval: they assume this was the only strategy tried. P3's
deflated Sharpe corrects for every variant tested.

Holding periods for the Australian tax view match sells to buys first-in first-out.
Information only: the engine does not model tax, and this is not tax advice.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

import polars as pl

from kestrel.backtest.engine import BacktestResult
from kestrel.backtest.metrics import compute_metrics, daily_equity

RESAMPLES = 2000
SEED = 20261105
Z95_ONE_SIDED = 1.6448536269514722
LONG_TERM = timedelta(days=365)
"""Australian CGT discount needs an asset held at least 12 months."""


@dataclass(frozen=True)
class YearRow:
    year: int
    strategy: float
    btc: float


@dataclass(frozen=True)
class Analysis:
    # Is the edge real?
    return_ci: tuple[float, float]
    """95% bootstrap interval for total return."""
    p_profit: float
    """Share of resampled histories that end above the starting cash."""
    expectancy_ci: tuple[float, float] | None
    """95% bootstrap interval for net expectancy per trade, in R (bracket runs)."""
    p_positive_expectancy: float | None
    min_track_record_days: float | None
    """Days of live results needed before this Sharpe is credible at 95% (None if the
    Sharpe isn't positive: no amount of time makes it credible)."""
    # Where the money comes from
    gross_profit: float
    """Profit before costs."""
    cost_per_round_trip_bps: float | None
    break_even_cost_bps: float | None
    """Cost per round trip at which the net profit would be zero."""
    beta: float | None
    correlation: float | None
    matched_btc_return: float
    """BTC held at the strategy's average exposure, rebalanced daily, no costs."""
    # Living with it
    worst_day: float
    worst_month: float
    expected_shortfall: float
    """Average of the worst 5% of days."""
    years: list[YearRow]
    payoff_ratio: float | None
    """Average win over average loss (bracket runs)."""
    profit_without_top5: float | None
    """Net profit with the five best trades removed (bracket runs). Negative means a few
    big wins carry the whole result. (A share of profit misleads when profit is near
    zero: +1.63 total made the top five look like 2,928% of it.)"""
    # Tax view (information only)
    holding_buckets: dict[str, int]
    long_term_gain_share: float | None
    """Share of realised gains from positions held more than 12 months."""
    open_at_end: bool
    """Some position was never sold, so its gain is unrealised."""
    resamples: int
    block_days: int


def analyse(
    result: BacktestResult,
    benchmark: BacktestResult,
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> Analysis:
    m = compute_metrics(result)
    daily = daily_equity(result).with_columns(ret=pl.col("equity").pct_change())
    rets = daily["ret"].drop_nulls().to_list()
    n = len(rets)
    rng = random.Random(seed)
    block = max(1, round(n ** (1 / 3)))

    # Circular block bootstrap of total return via prefix sums of log returns.
    logs = [math.log1p(r) for r in rets]
    prefix = [0.0]
    for v in logs + logs:  # doubled so a block may wrap around the end
        prefix.append(prefix[-1] + v)
    blocks = math.ceil(n / block)
    totals = []
    for _ in range(resamples):
        s = 0.0
        for _ in range(blocks):
            start = rng.randrange(n)
            s += prefix[start + block] - prefix[start]
        # Trim the last block's overshoot so every resample spans n days.
        totals.append(math.expm1(s * n / (blocks * block)))
    totals.sort()

    # Trades: iid bootstrap of R results.
    rs = [float(t.r_multiple) for t in result.trades]
    expectancy_ci: tuple[float, float] | None = None
    p_pos: float | None = None
    if len(rs) >= 2:
        means = sorted(sum(rng.choice(rs) for _ in rs) / len(rs) for _ in range(resamples))
        expectancy_ci = (_q(means, 0.025), _q(means, 0.975))
        p_pos = sum(x > 0 for x in means) / resamples

    # Minimum track record length (Bailey & Lopez de Prado), daily returns.
    mean = sum(rets) / n if n else 0.0
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0
    sr = mean / sd if sd > 0 else 0.0
    min_trl = (
        1 + (1 - m.skew * sr + (m.kurtosis - 1) / 4 * sr**2) * (Z95_ONE_SIDED / sr) ** 2
        if sr > 0
        else None
    )

    # Costs and break-even.
    traded = (
        result.summary.traded_notional
        if result.summary
        else sum(float(r.fill.quantity * r.fill.reference_price) for r in result.fills)
    )
    net = float(result.final_equity - result.initial_cash)
    gross = net + m.total_costs
    per_rt = 2 * m.total_costs / traded * 1e4 if traded > 0 else None
    break_even = max(0.0, 2 * gross / traded * 1e4) if traded > 0 else None

    # Against BTC.
    bench = daily_equity(benchmark).with_columns(btc=pl.col("equity").pct_change())
    joined = daily.join(bench.select("close_time", "btc"), on="close_time").drop_nulls(
        ["ret", "btc"]
    )
    beta = corr = None
    if joined.height > 2:
        var_b = joined["btc"].var()
        cov = joined.select(pl.cov("ret", "btc")).item()
        beta = cov / var_b if var_b else None
        corr = joined.select(pl.corr("ret", "btc")).item()
    matched = joined.select((1 + m.exposure * pl.col("btc")).product() - 1).item()

    # Tails and calendar years.
    worst_n = max(1, round(0.05 * n))
    shortfall = sum(sorted(rets)[:worst_n]) / worst_n if n else 0.0
    months = _period_returns(daily, "%Y-%m", float(result.initial_cash))
    years_s = _period_returns(daily, "%Y", float(result.initial_cash))
    years_b = _period_returns(daily_equity(benchmark), "%Y", float(benchmark.initial_cash))

    # Trades.
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    # A break-even trade (exactly 0R) is not a win, so "losses" can average zero: no ratio.
    payoff = (
        (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
        if wins and losses and sum(losses) < 0
        else None
    )
    pnls = sorted((float(t.pnl) for t in result.trades), reverse=True)
    without_top5 = sum(pnls[5:]) if len(pnls) > 5 else None

    buckets, lt_share, open_at_end = _holding_periods(result)
    return Analysis(
        return_ci=(_q(totals, 0.025), _q(totals, 0.975)),
        p_profit=sum(t > 0 for t in totals) / resamples,
        expectancy_ci=expectancy_ci,
        p_positive_expectancy=p_pos,
        min_track_record_days=min_trl,
        gross_profit=gross,
        cost_per_round_trip_bps=per_rt,
        break_even_cost_bps=break_even,
        beta=beta,
        correlation=corr,
        matched_btc_return=matched,
        worst_day=min(rets) if rets else 0.0,
        worst_month=min(months.values()) if months else 0.0,
        expected_shortfall=shortfall,
        years=[YearRow(int(y), r, years_b.get(y, math.nan)) for y, r in sorted(years_s.items())],
        payoff_ratio=payoff,
        profit_without_top5=without_top5,
        holding_buckets=buckets,
        long_term_gain_share=lt_share,
        open_at_end=open_at_end,
        resamples=resamples,
        block_days=block,
    )


def _q(sorted_values: list[float], p: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(p * len(sorted_values)))]


def _period_returns(daily: pl.DataFrame, fmt: str, start_equity: float) -> dict[str, float]:
    """Return per calendar period, from the last mark of each period to the next."""
    ends = (
        daily.with_columns(period=pl.col("close_time").dt.strftime(fmt))
        .group_by("period", maintain_order=True)
        .agg(pl.col("equity").last())
        .sort("period")
    )
    out: dict[str, float] = {}
    prev = start_equity
    for period, eq in ends.iter_rows():
        out[period] = eq / prev - 1
        prev = eq
    return out


@dataclass
class _Lot:
    quantity: float
    price: float
    bought: datetime


def _holding_periods(result: BacktestResult) -> tuple[dict[str, int], float | None, bool]:
    """FIFO-match sells to buys: how long each sold lot was held, and long-term gains."""
    buckets = {"under 1 month": 0, "1 to 12 months": 0, "over 12 months": 0}
    lots: list[_Lot] = []
    gains = long_gains = 0.0
    for rec in sorted(result.fills, key=lambda r: r.time):
        f = rec.fill
        qty, price = float(f.quantity), float(f.price)
        if f.side.value == "buy":
            lots.append(_Lot(qty, price, rec.time))
            continue
        while qty > 1e-12 and lots:
            lot = lots[0]
            take = min(qty, lot.quantity)
            held = rec.time - lot.bought
            gain = (price - lot.price) * take
            if held < timedelta(days=30):
                buckets["under 1 month"] += 1
            elif held > LONG_TERM:
                buckets["over 12 months"] += 1
            else:
                buckets["1 to 12 months"] += 1
            if gain > 0:
                gains += gain
                if held > LONG_TERM:
                    long_gains += gain
            qty -= take
            lot.quantity -= take
            if lot.quantity <= 1e-12:
                lots.pop(0)
    open_at_end = any(lot.quantity > 1e-12 for lot in lots)
    return buckets, (long_gains / gains if gains > 0 else None), open_at_end
