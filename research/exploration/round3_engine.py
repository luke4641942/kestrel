"""Exploration round 3: do round 2's best two rules survive the real engine (exact fills,
costs, minimum orders, 330 USDT) on exploration coins? Not saved, not trials.

Run: .venv/bin/python research/exploration/round3_engine.py
"""

from __future__ import annotations

import math
import random
import tomllib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import polars as pl

from kestrel.backtest.engine import run_backtest
from kestrel.backtest.metrics import compute_metrics
from kestrel.config import load_config
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.strategy import BuyAndHold

ROOT = Path(__file__).resolve().parents[2]


class BtcTrendVol:
    """Hold the coin while BTC closes above its b-day average and the coin is up over m
    days, sized to target/30-day volatility, capped at 1."""

    def __init__(
        self, symbol: str, btc: pl.DataFrame, b: int = 50, m: int = 30, target: float = 0.5
    ):
        self.symbol, self.btc, self.b, self.m, self.target = symbol, btc, b, m, target
        self.name = f"btc>sma{b} & up {m}d x vol{int(target * 100)} {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        joined = bars.select("open_time", "close").join(self.btc, on="open_time", how="left")
        c, btc = joined["close"].to_numpy(), joined["btc"].to_numpy()
        btc_ma = pl.Series(btc).rolling_mean(self.b, min_samples=self.b).to_numpy()
        up = np.full(len(c), np.nan)
        up[self.m :] = c[self.m :] > c[: -self.m]
        r = np.diff(np.log(c), prepend=np.nan)
        v = pl.Series(r).rolling_std(30, min_samples=30).to_numpy() * math.sqrt(365)
        w = np.where((btc > btc_ma) & (up == 1), np.minimum(1.0, self.target / v), 0.0)
        return pl.Series("weight", np.nan_to_num(w, nan=0.0))


class CrashRebound:
    """Hold the coin for ``hold`` days after it falls more than ``drop`` over ``days``."""

    def __init__(self, symbol: str, drop: float = 0.3, days: int = 7, hold: int = 3):
        self.symbol, self.drop, self.days, self.hold = symbol, drop, days, hold
        self.name = f"crash -{int(drop * 100)}%/{days}d hold {hold}d {symbol}"

    def target_weights(self, bars: pl.DataFrame) -> pl.Series:
        c = bars["close"].to_numpy()
        hit = np.zeros(len(c))
        hit[self.days :] = (c[self.days :] / c[: -self.days] - 1) < -self.drop
        w = pl.Series(hit).rolling_max(self.hold, min_samples=1).to_numpy()
        return pl.Series("weight", w.astype(float))


def main() -> None:
    cfg = load_config(ROOT / "kestrel.toml")
    split = tomllib.loads((ROOT / "research/exploration/split.toml").read_text())
    store = ParquetStore(ROOT / "data")
    end = cfg.backtest.holdout_start
    btc = store.read("BTCUSDT", Interval.D1, None, end).select(
        "open_time", pl.col("close").alias("btc")
    )
    sample = random.Random(1).sample(sorted(split["exploration"]), 40)
    rows = []
    for symbol in sample:
        bars = store.read(symbol, Interval.D1, None, end)
        if bars.height < 400:
            continue
        held = compute_metrics(
            run_backtest(BuyAndHold(symbol), bars, Interval.D1, cfg, Decimal(330))
        )
        for strategy in (BtcTrendVol(symbol, btc), CrashRebound(symbol)):
            m = compute_metrics(run_backtest(strategy, bars, Interval.D1, cfg, Decimal(330)))
            rows.append(
                {
                    "rule": strategy.name.rsplit(" ", 1)[0],
                    "coin": symbol,
                    "sharpe": m.sharpe,
                    "held_sharpe": held.sharpe,
                    "return": float(m.total_return),
                    "held_return": float(held.total_return),
                    "trades": m.trades if hasattr(m, "trades") else None,
                }
            )
    t = pl.DataFrame(rows).with_columns(gap=pl.col("sharpe") - pl.col("held_sharpe"))
    print(
        t.group_by("rule").agg(
            pl.len().alias("coins"),
            (pl.col("gap") > 0).mean().alias("beats_hold"),
            pl.col("gap").median().alias("median_gap"),
            pl.col("sharpe").median().alias("median_sharpe"),
            (pl.col("sharpe") > 0).mean().alias("positive"),
            pl.col("return").median().alias("median_return"),
            pl.col("held_return").median().alias("held_median_return"),
        )
    )
    print(f"({datetime.now(UTC):%Y-%m-%d %H:%M} UTC; 40 exploration coins drawn with seed 1)")


if __name__ == "__main__":
    main()
