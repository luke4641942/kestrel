"""Exploration round 2: monthly (and weekly) rotation among large coins (Luke, 2026-11-16).
Not trials, not evidence; how the graduates get confirmed is decided afterwards.

Coins: BTC, ETH, SOL (already heavily searched; in neither half of the split) and the
exploration-half large coins BNB, DOGE, LINK, DOT, LTC. Confirmation-half coins are never
read. Daily closes from 2018-01 to 2025-10 (before the holdout); a coin joins once it has
a year of history.

Each rule: at each rebalance close, rank the eligible coins by their return over the
lookback, hold the top k equally (each optionally sized to 50% annual volatility), with an
optional filter: the coin's own lookback return above zero (absolute momentum) and/or BTC
above its moving average; the rest in cash. Held from the next day. Costs: 7.5 bps fee
plus a half-spread (1 bp BTC/ETH, 3 bps the rest) on every change in weight.

Scores, each against BTC held and against holding the same coins in equal weight
(rebalanced daily, no costs): Sharpe, return, worst drawdown, and in each half
(2018-2021, 2022-2025-10) whether the Sharpe beats both.

Run: .venv/bin/python research/exploration/rotation_round1.py  (data/exploration/rotation1.csv)
"""

from __future__ import annotations

import itertools
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore

ROOT = Path(__file__).resolve().parents[2]
END = datetime(2025, 11, 1, tzinfo=UTC)
START = datetime(2018, 1, 1, tzinfo=UTC)
SPLIT_DAY = datetime(2022, 1, 1, tzinfo=UTC)
FEE = 7.5e-4
UNIVERSES = {
    "BTC/ETH/SOL": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "BTC/ETH": ["BTCUSDT", "ETHUSDT"],
    "BTC/ETH/SOL/BNB": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
    "8 large": [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT",
    ],
}  # fmt: skip


def closes(coins: list[str]) -> tuple[list[datetime], np.ndarray]:
    store = ParquetStore(ROOT / "data")
    frames = []
    for c in coins:
        b = store.read(c, Interval.D1, None, END).select("open_time", pl.col("close").alias(c))
        frames.append(b)
    df = frames[0]
    for f in frames[1:]:
        df = df.join(f, on="open_time", how="full", coalesce=True)
    df = df.sort("open_time").filter(pl.col("open_time") >= START - pl.duration(days=400))
    return df["open_time"].to_list(), df.select(coins).to_numpy().astype(float)


def sharpe(r: np.ndarray) -> float:
    r = r[~np.isnan(r)]
    return float(r.mean() / r.std() * math.sqrt(365)) if len(r) > 30 and r.std() > 0 else 0.0


def drawdown(r: np.ndarray) -> float:
    eq = np.cumprod(1 + np.nan_to_num(r))
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def simulate(
    times: list[datetime],
    px: np.ndarray,
    coins: list[str],
    lookback: int,
    k: int,
    every: int,
    absolute: bool,
    btc_ma: int | None,
    vol_size: bool,
) -> np.ndarray:
    n, m = px.shape
    ret = np.vstack([np.full(m, np.nan), px[1:] / px[:-1] - 1])
    logr = np.log(np.where(ret > -1, 1 + ret, np.nan))
    vol = pl.DataFrame(logr).select(
        pl.all().rolling_std(30, min_samples=30)
    ).to_numpy() * math.sqrt(365)
    btc = px[:, 0]
    ma = pl.Series(btc).rolling_mean(btc_ma, min_samples=btc_ma).to_numpy() if btc_ma else None
    spread = np.array([1e-4 if c in ("BTCUSDT", "ETHUSDT") else 3e-4 for c in coins])
    w = np.zeros(m)
    out = np.full(n, np.nan)
    for t in range(1, n):
        # earn on yesterday's weights, then maybe rebalance at today's close
        day = np.nansum(w * np.nan_to_num(ret[t]))
        out[t] = day
        if times[t] < START or t % every:
            continue
        past = px[t] / px[t - lookback] - 1 if t >= lookback else np.full(m, np.nan)
        eligible = ~np.isnan(past) & ~np.isnan(px[max(0, t - 365)])
        new = np.zeros(m)
        regime = ma is None or (not np.isnan(ma[t]) and btc[t] > ma[t])
        if regime and eligible.any():
            order = [i for i in np.argsort(-np.where(eligible, past, -np.inf)) if eligible[i]][:k]
            for i in order:
                if absolute and past[i] <= 0:
                    continue
                size = 1.0 / k
                if vol_size and not np.isnan(vol[t, i]) and vol[t, i] > 0:
                    size *= min(1.0, 0.5 / vol[t, i])
                new[i] = size
        cost = float(np.sum(np.abs(new - w) * (FEE + spread)))
        out[t] -= cost
        w = new
    keep = np.array([t >= START for t in times])
    return out[keep]


def main() -> None:
    rows = []
    for name, coins in UNIVERSES.items():
        times, px = closes(coins)
        keep = np.array([t >= START for t in times])
        kept_times = [t for t in times if t >= START]
        half = np.array([t < SPLIT_DAY for t in kept_times])
        ret = np.vstack([np.full(px.shape[1], np.nan), px[1:] / px[:-1] - 1])[keep]
        btc = ret[:, 0]
        ew = np.nanmean(ret, axis=1)  # equal weight of whichever coins trade that day
        base = {
            tag: (sharpe(btc[sel]), sharpe(ew[sel]))
            for tag, sel in (("all", slice(None)), ("first", half), ("second", ~half))
        }
        grid = itertools.product(
            (30, 60, 90, 180, 365), (1, 2, 3), (7, 30), (False, True), (None, 50, 100, 200),
            (False, True),
        )  # fmt: skip
        for lookback, k, every, absolute, btc_ma, vol_size in grid:
            if k > len(coins):
                continue
            r = simulate(times, px, coins, lookback, k, every, absolute, btc_ma, vol_size)
            s = {
                tag: sharpe(r[sel])
                for tag, sel in (("all", slice(None)), ("first", half), ("second", ~half))
            }
            rows.append(
                {
                    "universe": name,
                    "lookback": lookback,
                    "k": k,
                    "every": "weekly" if every == 7 else "monthly",
                    "absolute": absolute,
                    "btc_ma": btc_ma or 0,
                    "vol_size": vol_size,
                    "sharpe": s["all"],
                    "return": float(np.prod(1 + np.nan_to_num(r)) - 1),
                    "max_dd": drawdown(r),
                    "exposure": float("nan"),
                    "beats_btc_both_halves": s["first"] > base["first"][0]
                    and s["second"] > base["second"][0],
                    "beats_ew_both_halves": s["first"] > base["first"][1]
                    and s["second"] > base["second"][1],
                    "sharpe_first": s["first"],
                    "sharpe_second": s["second"],
                    "btc_sharpe": base["all"][0],
                    "ew_sharpe": base["all"][1],
                }
            )
        print(
            f"{name}: BTC Sharpe {base['all'][0]:.2f} "
            f"(halves {base['first'][0]:.2f}/{base['second'][0]:.2f}), "
            f"equal weight {base['all'][1]:.2f} ({base['first'][1]:.2f}/{base['second'][1]:.2f})"
        )
    table = pl.DataFrame(rows).drop("exposure")
    out = ROOT / "data" / "exploration" / "rotation1.csv"
    table.write_csv(out)
    robust = table.filter(
        pl.col("beats_btc_both_halves") & pl.col("beats_ew_both_halves") & (pl.col("max_dd") > -0.5)
    )
    print(
        f"\n{table.height} settings; {robust.height} beat BTC and equal weight in both halves "
        "with drawdown inside 50%"
    )
    with pl.Config(tbl_rows=25, tbl_width_chars=220):
        print(
            robust.sort("sharpe", descending=True)
            .head(20)
            .select(
                "universe",
                "lookback",
                "k",
                "every",
                "absolute",
                "btc_ma",
                "vol_size",
                "sharpe",
                "sharpe_first",
                "sharpe_second",
                "return",
                "max_dd",
            )
        )
        print("\nShare passing that filter, by lever:")
        for lever in ("universe", "every", "absolute", "btc_ma", "vol_size", "k", "lookback"):
            print(
                table.group_by(lever)
                .agg(
                    pl.len().alias("settings"),
                    (
                        pl.col("beats_btc_both_halves")
                        & pl.col("beats_ew_both_halves")
                        & (pl.col("max_dd") > -0.5)
                    )
                    .mean()
                    .alias("share_robust"),
                    pl.col("sharpe").median().alias("median_sharpe"),
                )
                .sort(lever)
            )


if __name__ == "__main__":
    main()
