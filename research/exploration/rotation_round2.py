"""Exploration round 2, part 2: extra filters on the two leading rotation shapes (not
evidence). G1: top 3 of the 8 large coins by 90-day return; G2: top 1 of BTC/ETH/SOL/BNB.
Both weekly, only while BTC is above its 50-day average, each sized to 50% volatility.
Extra filters tried, alone and together: BTC more than 3% above its average (a margin),
breadth (at least half the coins above their own 50-day average), stablecoin supply up
over the last 4 weeks (known the day before), BTC volatility under 80%.

Run: .venv/bin/python research/exploration/rotation_round2.py
"""

from __future__ import annotations

import itertools
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rotation_round1 as r1

from kestrel.data import stablecoins

SECOND = datetime(2022, 1, 1, tzinfo=UTC)


def run(times, px, coins, k, margin, breadth_min, stable, vol_cap):  # type: ignore[no-untyped-def]
    n, m = px.shape
    ret = np.vstack([np.full(m, np.nan), px[1:] / px[:-1] - 1])
    logr = np.log(np.where(ret > -1, 1 + ret, np.nan))
    vol = pl.DataFrame(logr).select(pl.all().rolling_std(30, min_samples=30)).to_numpy()
    vol *= math.sqrt(365)
    btc = px[:, 0]
    ma = pl.Series(btc).rolling_mean(50, min_samples=50).to_numpy()
    ma_each = pl.DataFrame(px).select(pl.all().rolling_mean(50, min_samples=50)).to_numpy()
    supply = stablecoins.load(r1.ROOT / "data")
    sd = dict(zip(supply["date"].to_list(), supply["supply"].to_list(), strict=True))
    spread = np.array([1e-4 if c in ("BTCUSDT", "ETHUSDT") else 3e-4 for c in coins])
    w, out, invested = np.zeros(m), np.full(n, np.nan), np.zeros(n)
    for t in range(1, n):
        out[t] = np.nansum(w * np.nan_to_num(ret[t]))
        invested[t] = w.sum()
        if times[t] < r1.START or t % 7:
            continue
        past = px[t] / px[t - 90] - 1 if t >= 90 else np.full(m, np.nan)
        elig = ~np.isnan(past) & ~np.isnan(px[max(0, t - 365)])
        on = not np.isnan(ma[t]) and btc[t] > ma[t] * (1 + margin)
        if on and breadth_min and elig.any():
            on = np.nanmean((px[t] > ma_each[t])[elig]) >= breadth_min
        if on and stable:
            d = times[t].date()
            a, b = sd.get(d - timedelta(days=1)), sd.get(d - timedelta(days=29))
            on = bool(a and b and a > b)
        if on and vol_cap:
            on = vol[t, 0] < vol_cap
        new = np.zeros(m)
        if on and elig.any():
            for i in [i for i in np.argsort(-np.where(elig, past, -np.inf)) if elig[i]][:k]:
                new[i] = (1 / k) * (min(1.0, 0.5 / vol[t, i]) if vol[t, i] > 0 else 0)
        out[t] -= float(np.sum(np.abs(new - w) * (r1.FEE + spread)))
        w = new
    keep = np.array([t >= r1.START for t in times])
    return out[keep], invested[keep]


def main() -> None:
    for label, universe, k in (
        ("G1 top 3 of 8 large", "8 large", 3),
        ("G2 top 1 of 4", "BTC/ETH/SOL/BNB", 1),
    ):
        coins = r1.UNIVERSES[universe]
        times, px = r1.closes(coins)
        kept = [t for t in times if t >= r1.START]
        first = np.array([t < SECOND for t in kept])
        rows = []
        for margin, breadth_min, stable, vol_cap in itertools.product(
            (0.0, 0.03), (0, 0.5), (False, True), (0, 0.8)
        ):
            r, inv = run(times, px, coins, k, margin, breadth_min, stable, vol_cap)
            rows.append(
                {
                    "filters": " + ".join(
                        x
                        for x, on in (
                            ("margin 3%", margin),
                            ("breadth", breadth_min),
                            ("stablecoins", stable),
                            ("vol<80%", vol_cap),
                        )
                        if on
                    )
                    or "(base)",
                    "sharpe": r1.sharpe(r),
                    "first": r1.sharpe(r[first]),
                    "second": r1.sharpe(r[~first]),
                    "return": float(np.prod(1 + np.nan_to_num(r)) - 1),
                    "max_dd": r1.drawdown(r),
                    "invested": float(np.mean(inv)),
                }
            )
        print(f"\n{label}")
        with pl.Config(tbl_rows=20, tbl_width_chars=200, fmt_str_lengths=50):
            print(pl.DataFrame(rows).sort("second", descending=True))


if __name__ == "__main__":
    main()
