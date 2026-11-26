"""Vectorised backtest engine: the same model as the event-driven engine, computed in
columns with polars instead of bar by bar, for fast research sweeps.

Timing and costs match the event engine exactly: the weight decided at bar t's close is
traded at bar t+1's open, paying fee, half-spread and square-root impact on the traded
notional. Between trades a position's weight drifts with price, and that is modelled in
closed form, so the turnover charged is what the event engine would actually trade:

    weight at bar i's close        w_c[i] = T[i] (1 + r_oc[i]) / (1 + T[i] r_oc[i])
    weight at bar i+1's open       w_o[i+1] = w_c[i] (1 + r_co[i+1]) / (1 + w_c[i] r_co[i+1])
    traded at bar i+1's open       d[i+1] = T[i+1] - w_o[i+1]
    growth over bar i+1            (1 + w_c[i] r_co[i+1]) (1 - |d| cost_rate) (1 + T[i+1] r_oc[i+1])

where T is the target weight, r_oc the open-to-close return and r_co the close-to-next-
open return. Equity is the cumulative product of the growth.

What it deliberately does not model, and the event engine does: the no-trade band (it
depends on the path), exchange minimums and quantity rounding, rejections, and bracket
(stop and target) strategies. Impact uses the notional from a first pass with linear
costs only, a second-order approximation. The parity test runs both engines where these
differences vanish (a large account, no band) and requires them to agree.
"""

from __future__ import annotations

from decimal import Decimal

import polars as pl

from kestrel.backtest.engine import BacktestResult, CostSummary, check_inputs
from kestrel.config import Config
from kestrel.data.panel import refuse_gaps
from kestrel.data.schema import Interval
from kestrel.provenance import code_version
from kestrel.strategy import BracketStrategy, Strategy, validated_weights


def run_vectorised(
    strategy: Strategy | BracketStrategy,
    bars: pl.DataFrame,
    interval: Interval,
    config: Config,
    initial_cash: Decimal,
    *,
    allow_holdout: bool = False,
) -> BacktestResult:
    if isinstance(strategy, BracketStrategy):
        raise ValueError(
            "the vectorised engine runs weight strategies only; stops and targets are "
            "path-dependent, so use the event engine"
        )
    if strategy.symbol.upper() not in config.instruments:
        raise ValueError(
            f"{strategy.symbol} has no [instruments] entry, so its spread depends on its "
            "volume; use the event engine"
        )
    used_holdout = check_inputs(bars, config, strategy.name, strategy.symbol, allow_holdout)
    refuse_gaps(bars, interval, strategy.symbol)
    if (bars["quote_volume"] <= 0).any():
        raise ValueError("bars with no volume can't be traded; use the event engine")

    c = config.costs
    fee = float(c.taker_fee_bps) / 1e4
    half = float(c.half_spread_bps) / 1e4
    coef = float(c.impact_coef_bps) / 1e4
    cash0 = float(initial_cash)

    frame = bars.select("open_time", "open", "close", "quote_volume").with_columns(
        weight=validated_weights(strategy, bars),
    )
    frame = (
        frame.with_columns(
            # Target held during bar i: decided at bar i-1's close. Nothing before bar 1.
            target=pl.col("weight").shift(1).fill_null(0.0),
            r_oc=pl.col("close") / pl.col("open") - 1,
            r_co=(pl.col("open") / pl.col("close").shift(1) - 1).fill_null(0.0),
            last_volume=pl.col("quote_volume").shift(1),
        )
        .with_columns(
            w_close=pl.col("target")
            * (1 + pl.col("r_oc"))
            / (1 + pl.col("target") * pl.col("r_oc")),
        )
        .with_columns(
            prev_w_close=pl.col("w_close").shift(1).fill_null(0.0),
        )
        .with_columns(
            w_open=pl.col("prev_w_close")
            * (1 + pl.col("r_co"))
            / (1 + pl.col("prev_w_close") * pl.col("r_co")),
            gap=1 + pl.col("prev_w_close") * pl.col("r_co"),
            hold=1 + pl.col("target") * pl.col("r_oc"),
        )
        .with_columns(delta=pl.col("target") - pl.col("w_open"))
    )

    buy = pl.col("delta") > 0

    def rate(slip: pl.Expr) -> pl.Expr:
        """Cost per unit of traded notional, exactly as CostModel.cost_rate."""
        return pl.when(buy).then((1 + slip) * (1 + fee) - 1).otherwise(1 - (1 - slip) * (1 - fee))

    def equity(cost_rate: pl.Expr) -> pl.Expr:
        growth = pl.col("gap") * (1 - pl.col("delta").abs() * cost_rate) * pl.col("hold")
        return cash0 * growth.cum_prod()

    # Pass 1, linear costs, gives the notional that sizes market impact in pass 2.
    frame = frame.with_columns(eq1=equity(rate(pl.lit(half))))
    frame = frame.with_columns(
        notional=(pl.col("delta").abs() * pl.col("eq1").shift(1).fill_null(cash0) * pl.col("gap")),
    ).with_columns(
        impact=(coef * (pl.col("notional") / pl.col("last_volume")).sqrt()).fill_null(0.0),
    )
    slip = half + pl.col("impact")
    frame = frame.with_columns(equity=equity(rate(slip)))
    frame = frame.with_columns(
        pre=pl.col("equity").shift(1).fill_null(cash0) * pl.col("gap"),
    ).with_columns(traded=pl.col("delta").abs() * pl.col("pre"))
    side = pl.when(buy).then(1.0).otherwise(-1.0)
    totals = frame.select(
        fees=(pl.col("traded") * (1 + side * slip) * fee).sum(),
        spread=(pl.col("traded") * half).sum(),
        impact=(pl.col("traded") * pl.col("impact")).sum(),
        traded=pl.col("traded").sum(),
        rebalances=(pl.col("delta").abs() > 1e-12).sum(),
    ).row(0, named=True)

    equity_frame = frame.select(
        close_time=pl.col("open_time") + interval.duration,
        equity=pl.col("equity"),
        cash=pl.col("equity") * (1 - pl.col("w_close")),
        position=pl.col("equity") * pl.col("w_close") / pl.col("close"),
        weight=pl.col("w_close"),
    )
    return BacktestResult(
        strategy=strategy.name,
        symbol=strategy.symbol,
        interval=interval,
        initial_cash=initial_cash,
        equity=equity_frame,
        start=bars["open_time"][0],
        used_holdout=used_holdout,
        config_fingerprint=config.fingerprint(),
        code=code_version(),
        engine="vectorised",
        account_currency=config.account_currency,
        summary=CostSummary(
            fees=totals["fees"],
            spread_costs=totals["spread"],
            impact_costs=totals["impact"],
            traded_notional=totals["traded"],
            rebalances=int(totals["rebalances"]),
        ),
    )
