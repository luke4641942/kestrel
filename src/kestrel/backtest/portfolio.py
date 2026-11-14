"""Portfolio engine: one strategy holding several coins at once (cross-sectional).

Same timing as the single-coin engine: a decision at day t's close fills at day t+1's
open, with market impact sized from each coin's last closed bar and a spread set by how
much the coin trades (kestrel.backtest.spread). Orders go through the same ``SimVenue``.

Decisions, not daily targets. A strategy returns rows (open_time, symbol, weight): each
open_time present is a decision (a rebalance), and coins it doesn't list are sold; a row
with a null symbol marks a decision to hold no coins. Between decisions nothing trades
and weights drift with prices: a weekly strategy trades weekly. Weights are in [0, 1]
and sum to at most 1 (long only, spot, the rest in cash). On a rebalance every sell goes
first, so sales fund the buys; a coin within the no-trade band of its target is left
alone.

Keeping a position. A row whose weight is null keeps that coin exactly as held, with no
resizing: a strategy that adds a coin needn't trim the ones it already holds back to
equal weight. (A null weight for a coin not held means nothing to keep.)

Limit entries. A decision row may carry a ``limit`` price for a coin not already held: a
buy order resting below the market for the next bar only. It fills if that bar's low
reaches the limit, at the lower of the limit and the open (a gap down through it fills
at the open), after the open's sells and market buys, with the same costs as a market
order (conservative: a resting order would usually save the spread). Unfilled, it
lapses; nothing is retried until the strategy decides again. The optional column is
null for ordinary rows.

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
LIMIT = "limit"
"""Optional decision column: a limit price for a new buy (see the module docstring)."""


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
    if set(frame.columns) - {LIMIT} != set(WEIGHT_SCHEMA):
        raise ValueError(f"{name}: decisions need columns {sorted(WEIGHT_SCHEMA)}")
    if LIMIT not in frame.columns:
        frame = frame.with_columns(pl.lit(None, pl.Float64).alias(LIMIT))
    out = frame.select(
        [pl.col(c).cast(t) for c, t in WEIGHT_SCHEMA.items()] + [pl.col(LIMIT).cast(pl.Float64)]
    )
    if out["weight"].is_nan().any():
        raise ValueError(f"{name}: weights contain NaN")
    if out.filter(pl.col("weight").is_null() & pl.col("symbol").is_null()).height:
        raise ValueError(f"{name}: a null weight (keep) needs a coin")
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
    duplicated = panel.filter(pl.struct("symbol", "open_time").is_duplicated())
    if duplicated.height:
        coins = ", ".join(sorted(duplicated["symbol"].unique().to_list()))
        raise ValueError(f"duplicate bars for {coins}; run the quality check (not repaired)")
    dates: list[datetime] = panel["open_time"].unique().sort().to_list()
    used_holdout = check_inputs(
        pl.DataFrame({"open_time": dates}), config, strategy.name, UNIVERSE, allow_holdout
    )
    decisions = validated_decisions(strategy, panel)
    priced = with_half_spread(
        panel,
        config.universe.volume_window_days,
        [(float(v), float(bps) / 1e4) for v, bps in config.universe.spread_tiers],
        ["symbol", "history"],
    )
    # Per coin: each bar's prices, and the volume and spread known at its close.
    bars: dict[tuple[str, datetime], tuple[Decimal, Decimal, Decimal, Decimal]] = {
        (s, t): (Decimal(str(o)), Decimal(str(c)), Decimal(str(v)), Decimal(str(h)))
        for s, t, o, c, v, h in priced.select(
            "symbol", "open_time", "open", "close", "quote_volume", "half_spread"
        ).iter_rows()
    }
    targets: dict[datetime, dict[str, Decimal | None]] = {}
    limits: dict[datetime, dict[str, Decimal]] = {}
    for t, s, w, limit in decisions.iter_rows():
        chosen = targets.setdefault(t, {})
        if s is not None:
            chosen[s] = None if w is None else Decimal(str(w))  # None: keep as held
            if limit is not None:
                limits.setdefault(t, {})[s] = Decimal(str(limit))
    lows: dict[tuple[str, datetime], Decimal] = (
        {
            (s, t): Decimal(str(low))
            for s, t, low in panel.select("symbol", "open_time", "low").iter_rows()
        }
        if limits
        else {}
    )

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
    stranded: set[str] = set()  # held coins whose forced sale was refused; never retried
    rows: list[tuple[Decimal, Decimal, Decimal]] = [(initial_cash, initial_cash, Decimal(0))]
    previous: dict[str, Decimal | None] = {}

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
        # A refused sale is recorded once: retrying at the same stale price can't succeed.
        for s, qty in venue.holdings().items():
            if (s, day) not in bars and s not in stranded:
                seen = last_bar[s]
                close = bars[(s, seen)][1]
                order = submit(s, Side.SELL, qty, close, seen + interval.duration, seen)
                if isinstance(order, Fill):
                    result.forced_exits += 1
                    log.info("forced exit: %s had no bar on %s", s, day)
                else:
                    stranded.add(s)

        # A decision at yesterday's close trades at today's open.
        if before in targets:
            target = targets[before]
            opens = {
                s: bars[(s, day)][0]
                for s in set(target) | set(venue.holdings())
                if (s, day) in bars
            }
            # A stranded coin has no price today: it counts at its last close and can't trade.
            holdings = venue.holdings()
            equity = venue.cash() + sum(
                q * (opens[s] if s in opens else bars[(s, last_bar[s])][1])
                for s, q in holdings.items()
            )
            current = {s: q * opens[s] / equity for s, q in holdings.items() if s in opens}
            wanted: dict[str, Decimal] = {}
            for s in set(target) | set(current):
                if s in opens:
                    w = target.get(s, Decimal(0))
                    wanted[s] = current.get(s, Decimal(0)) if w is None else w
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
            resting = limits.get(before, {})
            buys = sorted(s for s, ch in changes.items() if ch > band)
            # Market buys at the open first; limit buys rest during the bar.
            for s in [b for b in buys if b not in resting or b in current] + [
                b for b in buys if b in resting and b not in current
            ]:
                price = opens[s]
                if s in resting and s not in current:
                    low = lows[(s, day)]
                    if low > resting[s]:
                        continue  # the bar never reached the limit: the order lapses
                    price = min(resting[s], opens[s])
                _, _, volume, spread = bars[(s, before)]
                notional = min(changes[s] * equity, venue.cash())
                rate = (
                    model.cost_rate(Side.BUY, notional, volume, spread)
                    if volume > 0
                    else Decimal(0)
                )
                if notional > 0:
                    submit(s, Side.BUY, notional / (1 + rate) / price, price, day, before)
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
