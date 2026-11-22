"""Why does G1 work? (TODO; exploration mode: not saved, not trials, never evidence.
Nothing here can change G1's frozen rule.)

G1 (btc-rotation-8): each Sunday, while BTC closes more than 3% above its 50-day average,
hold the top 3 of 8 large coins by 90-day return, each sized to 50% volatility; else cash.
Its parts, taken apart on the real portfolio engine (330 USDT, costs, minimum orders),
G1's own coins, 2018-01 to 2025-10:

- G1 itself (checked to reproduce kestrel.strategies.btc_rotation exactly);
- filter alone: while the filter is on, hold every eligible coin, equal shares, same sizing;
- filter + random 3: the filter with 3 coins drawn at random each Sunday (100 seeds);
- ranking alone: G1 without the BTC filter;
- risk-adjusted ranking: the filter, ranked by 90-day return over 90-day volatility;
- every coin, no filter: the equal-weight basket, weekly, same sizing (a control).

Run: .venv/bin/python research/exploration/why_g1.py
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl

from kestrel.backtest.engine import BacktestResult, run_backtest
from kestrel.backtest.metrics import compute_metrics
from kestrel.backtest.portfolio import run_portfolio
from kestrel.config import load_config
from kestrel.data.binance_vision import Month
from kestrel.data.panel import load_panel
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategies.basket import BY, DECISIONS
from kestrel.strategies.btc_rotation import BTC, LARGE_8, MIN_HISTORY, BtcRegimeRotation
from kestrel.strategy import BuyAndHold

ROOT = Path(__file__).resolve().parents[2]
D1 = Interval.D1
CASH = Decimal(330)


@dataclass
class Variant:
    """G1's rule with switches. ``rank``: momentum, risk (return / volatility), random, all."""

    label: str
    use_filter: bool = True
    rank: str = "momentum"
    top_n: int = 3
    seed: int = 0
    symbol: str = "UNIVERSE"

    @property
    def name(self) -> str:
        return self.label

    def target_weights(self, panel: pl.DataFrame) -> pl.DataFrame:
        close = pl.col("close")
        scored = (
            panel.filter(pl.col("symbol").is_in(LARGE_8))
            .sort("open_time")
            .with_columns(
                _r=(close / close.shift(90)).over(BY) - 1,
                _age=pl.int_range(1, pl.len() + 1).over(BY),
                _vol=close.log().diff().rolling_std(30, min_samples=30).over(BY) * math.sqrt(365),
                _vol90=close.log().diff().rolling_std(90, min_samples=90).over(BY) * math.sqrt(365),
            )
        )
        btc = (
            scored.filter(pl.col("symbol") == BTC)
            .with_columns(_ma=close.rolling_mean(50, min_samples=50).over(BY))
            .select("open_time", _on=close > pl.col("_ma") * 1.03)
        )
        sundays = scored.filter(pl.col("open_time").dt.weekday() == 7).join(
            btc, on="open_time", how="left"
        )
        ok = (
            pl.col("_r").is_not_null()
            & (pl.col("_age") >= MIN_HISTORY)
            & pl.col("_vol").is_not_null()
            & (pl.col("_vol") > 0)
        )
        if self.use_filter:
            ok = ok & pl.col("_on").fill_null(False)
        eligible = sundays.filter(ok)
        if self.rank == "risk":
            eligible = eligible.filter(pl.col("_vol90").is_not_null()).with_columns(
                _r=pl.col("_r") / pl.col("_vol90")
            )
        if self.rank == "random":
            rnd = random.Random(self.seed)
            eligible = eligible.sort("open_time", "symbol").with_columns(
                _r=pl.Series([rnd.random() for _ in range(eligible.height)])
            )
        ranked = eligible.sort(["open_time", "_r", "symbol"], descending=[False, True, False])
        if self.rank == "all":
            chosen = ranked.with_columns(_n=pl.len().over("open_time"))
        else:
            chosen = ranked.group_by("open_time", maintain_order=True).head(self.top_n)
            chosen = chosen.with_columns(_n=pl.lit(self.top_n))
        chosen = chosen.with_columns(
            weight=pl.min_horizontal(pl.lit(1.0), 0.5 / pl.col("_vol")) / pl.col("_n")
        ).select(list(DECISIONS))
        days = sundays.select("open_time").unique()
        idle = days.join(chosen, on="open_time", how="anti").select(
            "open_time", symbol=pl.lit(None, pl.Utf8), weight=pl.lit(0.0)
        )
        out = pl.concat([chosen, idle]).select([pl.col(c).cast(t) for c, t in DECISIONS.items()])
        return out.sort("open_time", "symbol", nulls_last=True)


SPLIT = datetime(2022, 1, 1, tzinfo=UTC)


def sharpe(result: BacktestResult, lo: datetime | None = None, hi: datetime | None = None) -> float:
    """Annualised Sharpe of the daily (00:00 UTC) equity, optionally between two dates."""
    eq = result.equity.select("close_time", "equity")
    if lo is not None:
        eq = eq.filter(pl.col("close_time") >= lo)
    if hi is not None:
        eq = eq.filter(pl.col("close_time") < hi)
    daily = eq.group_by_dynamic("close_time", every="1d").agg(pl.col("equity").last())
    r = daily["equity"].pct_change().drop_nulls()
    sd = r.std()
    return float(r.mean()) / float(sd) * math.sqrt(365) if sd else 0.0  # type: ignore[arg-type]


def row(label: str, result: BacktestResult) -> dict[str, object]:
    m = compute_metrics(result)
    return {
        "variant": label,
        "sharpe": round(m.sharpe, 2),
        "2018-21": round(sharpe(result, hi=SPLIT), 2),
        "2022-25": round(sharpe(result, lo=SPLIT), 2),
        "return": f"{m.total_return:+.0%}",
        "max_dd": f"{m.max_drawdown:.0%}",
        "exposure": f"{m.exposure:.0%}",
        "trades": m.trades,
    }


def main() -> None:
    cfg = load_config(ROOT / "kestrel.toml")
    store = ParquetStore(ROOT / "data")
    panel = load_panel(store, list(LARGE_8), D1, Month.parse("2018-01"), Month.parse("2025-10"))

    # The switches at G1's settings must reproduce G1's own decisions.
    mine = Variant("G1").target_weights(panel)
    assert mine.equals(BtcRegimeRotation().target_weights(panel)), "the copy drifted from G1"

    btc_bars = store.read(BTC, D1, None, cfg.backtest.holdout_start).filter(
        pl.col("open_time") >= datetime(2018, 1, 1, tzinfo=UTC)
    )
    variants = [
        Variant("G1 (filter + momentum top 3)"),
        Variant("filter alone (all eligible)", rank="all"),
        Variant("ranking alone (no filter)", use_filter=False),
        Variant("filter + risk-adjusted top 3", rank="risk"),
        Variant("every coin, no filter", use_filter=False, rank="all"),
    ]
    rows = [row(v.label, run_portfolio(v, panel, D1, cfg, CASH)) for v in variants]
    rows.append(row("BTC held", run_backtest(BuyAndHold(BTC), btc_bars, D1, cfg, CASH)))

    randoms = []
    for seed in range(100):
        res = run_portfolio(Variant("random", rank="random", seed=seed), panel, D1, cfg, CASH)
        randoms.append((sharpe(res), sharpe(res, hi=SPLIT), sharpe(res, lo=SPLIT)))

    pl.Config.set_tbl_cols(12)
    pl.Config.set_tbl_width_chars(160)
    pl.Config.set_tbl_hide_dataframe_shape(True)
    print(pl.DataFrame(rows))
    g1 = (
        sharpe_of(rows[0], "sharpe"),
        sharpe_of(rows[0], "2018-21"),
        sharpe_of(rows[0], "2022-25"),
    )
    for i, name in enumerate(("2018-25", "2018-21", "2022-25")):
        s = sorted(x[i] for x in randoms)
        print(
            f"filter + random 3, {name}: median Sharpe {s[50]:.2f} (5-95%: {s[5]:.2f} to "
            f"{s[94]:.2f}); G1 ({g1[i]:.2f}) beats {sum(x[i] < g1[i] for x in randoms)} of 100"
        )
    print(f"({datetime.now(UTC):%Y-%m-%d %H:%M} UTC; exploration only: not a trial, not evidence)")


def sharpe_of(r: dict[str, object], key: str) -> float:
    v = r[key]
    assert isinstance(v, float)
    return v


if __name__ == "__main__":
    main()
