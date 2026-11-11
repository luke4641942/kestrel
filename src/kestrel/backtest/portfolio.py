"""Portfolio engine: one strategy holding several coins at once (cross-sectional).

Same timing as the single-coin engine: a decision at day t's close fills at day t+1's
open, with market impact sized from each coin's last closed bar and its own estimated
spread (kestrel.backtest.spread). Orders go through the same ``SimVenue``.

Decisions, not daily targets. A strategy returns rows (open_time, symbol, weight): each
open_time present is a decision (a rebalance), and coins it doesn't list are sold; a row
with a null symbol marks a decision to hold no coins. Between decisions nothing trades
and weights drift with prices: a weekly strategy trades weekly. Weights are in [0, 1]
and sum to at most 1 (long only, spot, the rest in cash). On a rebalance every sell goes
first, so sales fund the buys; a coin within the no-trade band of its target is left
alone.

Gaps and delistings. A coin held when its bars stop (delisted, or a gap that later
reopens as a different coin under the same symbol) is sold at its last close, the
closest the data allows, and counted as a forced exit. A strategy may only target a
coin that has a bar at the decision.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

import polars as pl

from kestrel.backtest.costs import Fill, Rejection, Side
from kestrel.backtest.engine import BacktestResult, FillRecord, RejectionRecord, check_inputs
from kestrel.backtest.spread import with_half_spread
from kestrel.config import Config
from kestrel.data.schema import Interval
from kestrel.execution.venue import SimVenue
from kestrel.provenance import code_version
from kestrel.strategy import UNIVERSE, LookaheadError, PortfolioStrategy

log = logging.getLogger(__name__)

WEIGHT_SCHEMA: dict[str, pl.DataType] = {
    "open_time": pl.Datetime("us", "UTC"),
    "symbol": pl.Utf8(),
    "weight": pl.Float64(),
}


def validated_decisions(
    strategy: PortfolioStrategy, panel: pl.DataFrame, checkpoints: int = 8
) -> pl.DataFrame:
    """The strategy's decisions, checked for shape, weights and lookahead."""
    decisions = _normalised(strategy.name, strategy.target_weights(panel))
    held = decisions.filter(pl.col("symbol").is_not_null())
    if held.height and (held["weight"].min() < 0 or held["weight"].max() > 1):  # type: ignore[operator]
        raise ValueError(f"{strategy.name}: weights must be in [0, 1] (long-only spot)")
    over = (
        held.group_by("open_time").agg(pl.col("weight").sum()).filter(pl.col("weight") > 1 + 1e-9)
    )
    if over.height:
        raise ValueError(f"{strategy.name}: weights sum above 1 on {over['open_time'][0]}")
    missing = held.join(panel.select("open_time", "symbol"), on=["open_time", "symbol"], how="anti")
    if missing.height:
        row = missing.row(0, named=True)
        raise ValueError(
            f"{strategy.name}: targets {row['symbol']} on {row['open_time']}, where it has no bar"
        )
    _check_prefixes(strategy, panel, decisions, checkpoints)
    return decisions


def _normalised(name: str, frame: pl.DataFrame) -> pl.DataFrame:
    if set(frame.columns) != set(WEIGHT_SCHEMA):
        raise ValueError(f"{name}: decisions need columns {sorted(WEIGHT_SCHEMA)}")
    out = frame.select([pl.col(c).cast(t) for c, t in WEIGHT_SCHEMA.items()])
    if out["weight"].null_count() or out["weight"].is_nan().any():
        raise ValueError(f"{name}: weights contain nulls or NaN")
    return out.sort("open_time", "symbol", nulls_last=True)


def _check_prefixes(
    strategy: PortfolioStrategy, panel: pl.DataFrame, full: pl.DataFrame, checkpoints: int
) -> None:
    """Decisions made on a truncated history must equal the same decisions made on all
    of it: a strategy that changes its past choices when given the future is using it."""
    dates = panel["open_time"].unique().sort()
    n = dates.len()
    cuts = sorted({dates[max(1, n * k // (checkpoints + 1))] for k in range(1, checkpoints + 1)})
    for cut in cuts:
        early = _normalised(
            strategy.name, strategy.target_weights(panel.filter(pl.col("open_time") < cut))
        )
        expected = full.filter(pl.col("open_time") < cut)
        if not early.equals(expected):
            raise LookaheadError(
                f"{strategy.name}: decisions before {cut} change when later bars are removed. "
                "It is using information from the future."
            )


def run_portfolio(
    strategy: PortfolioStrategy,
    panel: pl.DataFrame,
    interval: Interval,
    config: Config,
    initial_cash: Decimal,
    *,
    allow_holdout: bool = False,
) -> BacktestResult:
    """Run ``strategy`` over ``panel`` (kestrel.data.panel: sorted, with histories)."""
    dates: list[datetime] = panel["open_time"].unique().sort().to_list()
    used_holdout = check_inputs(
        pl.DataFrame({"open_time": dates}), config, strategy.name, UNIVERSE, allow_holdout
    )
    decisions = validated_decisions(strategy, panel)
    priced = with_half_spread(
        panel,
        config.universe.spread_window_days,
        float(config.costs.half_spread_bps) / 1e4,
        ["symbol", "history"],
    )
    # Per coin: each bar's prices, and the volume and spread known at its close.
    bars: dict[tuple[str, datetime], tuple[Decimal, Decimal, Decimal, Decimal]] = {
        (s, t): (Decimal(str(o)), Decimal(str(c)), Decimal(str(v)), Decimal(str(h)))
        for s, t, o, c, v, h in priced.select(
            "symbol", "open_time", "open", "close", "quote_volume", "half_spread"
        ).iter_rows()
    }
    targets: dict[datetime, dict[str, Decimal]] = {}
    for t, s, w in decisions.iter_rows():
        chosen = targets.setdefault(t, {})
        if s is not None:
            chosen[s] = Decimal(str(w))

    venue = SimVenue(config, initial_cash, rules=config.rules_for)
    band = config.backtest.no_trade_band
    result = BacktestResult(
        strategy=strategy.name,
        symbol=UNIVERSE,
        interval=interval,
        initial_cash=initial_cash,
        equity=pl.DataFrame(),
        start=dates[0],
        used_holdout=used_holdout,
        config_fingerprint=config.fingerprint(),
        code=code_version(),
        account_currency=config.account_currency,
    )
    last_bar: dict[str, datetime] = {}  # each held coin's latest bar
    rows: list[tuple[Decimal, Decimal, Decimal]] = [(initial_cash, initial_cash, Decimal(0))]
    previous: dict[str, Decimal] = {}

    def submit(
        symbol: str, side: Side, qty: Decimal, price: Decimal, at: datetime, known: datetime
    ) -> Fill | Rejection:
        _, _, volume, spread = bars[(symbol, known)]
        venue.set_market(symbol, price, volume, spread)
        order = venue.submit_market(symbol, side, qty)
        if isinstance(order, Fill):
            result.fills.append(FillRecord(at, order))
        else:
            result.rejections.append(RejectionRecord(at, order))
        return order

    for i in range(1, len(dates)):
        day, before = dates[i], dates[i - 1]

        # Forced exits: a held coin with no bar today is sold at its last close.
        for s, qty in venue.holdings().items():
            if (s, day) not in bars:
                seen = last_bar[s]
                close = bars[(s, seen)][1]
                order = submit(s, Side.SELL, qty, close, seen + interval.duration, seen)
                if isinstance(order, Fill):
                    result.forced_exits += 1
                    log.info("forced exit: %s had no bar on %s", s, day)

        # A decision at yesterday's close trades at today's open.
        if before in targets:
            target = targets[before]
            opens = {
                s: bars[(s, day)][0]
                for s in set(target) | set(venue.holdings())
                if (s, day) in bars
            }
            equity = venue.cash() + sum(q * opens[s] for s, q in venue.holdings().items())
            current = {s: q * opens[s] / equity for s, q in venue.holdings().items()}
            wanted = {
                s: target.get(s, Decimal(0)) for s in set(target) | set(current) if s in opens
            }
            changes = {s: w - current.get(s, Decimal(0)) for s, w in wanted.items()}
            # Resizing within the band is skipped (and counted when the target changed);
            # a coin dropped from the target is always sold in full.
            for s, change in changes.items():
                new = wanted[s] != previous.get(s, Decimal(0))
                if wanted[s] > 0 and 0 < abs(change) <= band and new:
                    result.band_skips += 1
            sells = [
                s for s, ch in changes.items() if ch < -band or (wanted[s] == 0 and s in current)
            ]
            for s in sorted(sells):
                held = venue.position(s)
                qty = held if wanted[s] == 0 else min(held, -changes[s] * equity / opens[s])
                submit(s, Side.SELL, qty, opens[s], day, before)
            model = venue.cost_model()
            for s in sorted(s for s, ch in changes.items() if ch > band):
                _, _, volume, spread = bars[(s, before)]
                notional = min(changes[s] * equity, venue.cash())
                rate = (
                    model.cost_rate(Side.BUY, notional, volume, spread)
                    if volume > 0
                    else Decimal(0)
                )
                if notional > 0:
                    submit(s, Side.BUY, notional / (1 + rate) / opens[s], opens[s], day, before)
            previous = target

        for s in venue.holdings():
            if (s, day) in bars:
                last_bar[s] = day
        # A coin whose forced sale was refused (e.g. below the exchange minimum) is
        # valued at its last close.
        value = sum(q * bars[(s, last_bar[s])][1] for s, q in venue.holdings().items())
        rows.append((venue.cash() + value, venue.cash(), Decimal(len(venue.holdings()))))

    result.equity = pl.DataFrame(
        {
            "close_time": [d + interval.duration for d in dates],
            "equity": [float(r[0]) for r in rows],
            "cash": [float(r[1]) for r in rows],
            "position": [float(r[2]) for r in rows],
        }
    ).with_columns(
        weight=pl.when(pl.col("equity") > 0)
        .then(1 - pl.col("cash") / pl.col("equity"))
        .otherwise(0.0)
    )
    return result
