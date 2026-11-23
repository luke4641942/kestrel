"""Run a forex note (P6): ``kestrel fx-backtest``.

The forex counterpart of ``kestrel backtest --research``: a frozen note only, its
implementation an ``fx:`` strategy, every pair in ``[fx] pairs``, data before the forex
holdout only. The strategy may see history from ``warmup_from`` (its note says so); the
engine trades from ``start``. The run is saved (kestrel.backtest.fx_runs), counts as a trial
in the forex pool, and its Sharpe is deflated against that pool, with every pool's total
reported beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from kestrel.backtest.fx_engine import run_fx
from kestrel.backtest.fx_runs import FxRunStore, compute_fx_metrics, format_fx_report
from kestrel.backtest.runs import RunStore
from kestrel.config import Config
from kestrel.data import fx
from kestrel.data.binance_vision import Month
from kestrel.data.rates import Rates
from kestrel.fx_strategy import FX_STRATEGIES, make_fx_strategy, resolve_fx_params
from kestrel.fx_strategy import validated_fx_weights as validate
from kestrel.research import ResearchStore
from kestrel.validation.deflated import deflated_sharpe, every_pool, format_pools
from kestrel.validation.log import ValidationLog


@dataclass(frozen=True)
class FxRun:
    run_id: str
    report: str


def _month_start(text: str) -> datetime:
    m = Month.parse(text)
    return datetime(m.year, m.month, 1, tzinfo=UTC)


def fx_backtest(
    store: ResearchStore,
    research: str,
    data_dir: Path | str,
    config: Config,
    start: str,
    end: str,
    cash: float,
    params: dict[str, int] | None = None,
    warmup_from: str | None = None,
) -> FxRun:
    if config.fx is None:
        raise ValueError("no [fx] table in the config")
    s, note = store.require_testable(research)
    key = note.implementation
    if key not in FX_STRATEGIES:
        raise ValueError(f"{research} names {key}, not a forex strategy (fx:...)")
    chosen = resolve_fx_params(key, params)
    first = _month_start(start)
    after = Month.parse(end).next()
    until = datetime(after.year, after.month, 1, tzinfo=UTC)
    warm = _month_start(warmup_from) if warmup_from else first
    if warm > first:
        raise ValueError("the warm-up starts after the run does")
    if until > config.fx.holdout_start:
        raise ValueError(
            f"the run reaches the forex holdout (from {config.fx.holdout_start:%Y-%m-%d}); "
            "research stays before it"
        )

    seen: dict[str, pl.DataFrame] = {}
    dropped = 0
    for pair in config.fx.pairs:
        raw = fx.load(data_dir, pair)
        raw = raw.filter((pl.col("open_time") >= warm) & (pl.col("open_time") < until))
        frame, n = fx.trading_days(raw)
        dropped += n
        seen[pair] = frame
    rates = Rates.load(data_dir)
    strategy = make_fx_strategy(key, chosen)
    weights = validate(strategy, seen, rates, float(config.fx.max_gross_leverage))

    trading = {p: f.filter(pl.col("open_time") >= first) for p, f in seen.items()}
    result = run_fx(
        strategy.name,
        trading,
        weights.filter(pl.col("open_time") >= first),
        config.fx,
        float(config.backtest.no_trade_band),
        rates,
        cash,
        config=config,
    )
    metrics = compute_fx_metrics(result)
    run_id = FxRunStore(data_dir).save(
        result,
        metrics,
        params=chosen,
        start=start,
        end=end,
        research={"slug": s.slug, "version": note.version},
        detail={
            "warmup_from": warmup_from,
            "weekend_candles_dropped": dropped,
            "rate_days_carried": rates.carried_days,
            "financing_markup_pct": float(config.fx.financing_markup_pct),
        },
    )
    store.mark_backtested(s.slug, note.version)

    pools = every_pool(
        [*RunStore(data_dir).summaries(), *FxRunStore(data_dir).summaries()],
        ValidationLog(data_dir).studies(),
    )
    dsr = deflated_sharpe(
        metrics.sharpe, metrics.days, metrics.skew, metrics.kurtosis, pools["forex"]
    )
    extra = [
        "",
        f"  Deflated Sharpe   {'n/a' if dsr is None else f'{dsr:.0%}'} (95% needed)",
        f"  {format_pools(pools, 'forex')}",
        f"  Data              weekend candles dropped: {dropped}; days on a carried-forward "
        f"interest rate: {rates.carried_days}",
        f"  Run {run_id}, logged as a trial of {s.slug} v{note.version} (forex pool)",
    ]
    return FxRun(run_id, format_fx_report(result, metrics, extra))
