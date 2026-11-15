"""Exploration round 2: grids around round 1's four best families, and combinations, on the
exploration half only (not trials, not evidence). Adds each rule's own median Sharpe and
the share of coins where it is positive, so a rule that only loses less than holding is
visible as such.

Run: .venv/bin/python research/exploration/round2.py   (writes data/exploration/round2.csv)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from round1 import ROOT, hold_on, load, sharpe, shift, sma, vol


def score(coins: dict[str, dict[str, np.ndarray]], rule) -> dict[str, float]:  # type: ignore[no-untyped-def]
    gaps, firsts, seconds, own, both = [], [], [], [], 0
    for d in coins.values():
        w = np.clip(np.nan_to_num(rule(d), nan=0.0), 0, 1)
        ret = np.diff(d["close"], prepend=np.nan) / shift(d["close"], 1)
        turnover = np.abs(np.diff(w, prepend=0.0))
        strat = shift(w, 1) * ret - shift(turnover * d["cost"], 1)
        half = len(ret) // 2
        s, h = sharpe(strat), sharpe(ret)
        s1, h1 = sharpe(strat[:half]), sharpe(ret[:half])
        s2, h2 = sharpe(strat[half:]), sharpe(ret[half:])
        if s is None or h is None:
            continue
        gaps.append(s - h)
        own.append(s)
        g1 = None if s1 is None or h1 is None else s1 - h1
        g2 = None if s2 is None or h2 is None else s2 - h2
        if g1 is not None:
            firsts.append(g1)
        if g2 is not None:
            seconds.append(g2)
        both += bool(g1 is not None and g2 is not None and g1 > 0 and g2 > 0)
    n = len(gaps)
    return {
        "coins": n,
        "beats_hold": sum(g > 0 for g in gaps) / n,
        "both_halves": both / n,
        "median_gap": float(np.median(gaps)),
        "gap_first": float(np.median(firsts)),
        "gap_second": float(np.median(seconds)),
        "median_sharpe": float(np.median(own)),
        "positive_sharpe": sum(x > 0 for x in own) / n,
    }


def grid() -> dict[str, tuple[str, object]]:
    out: dict[str, tuple[str, object]] = {}
    for b in (20, 30, 50, 70, 100):
        for m in (10, 20, 30, 40, 60):
            for target in (None, 0.5):
                tag = "" if target is None else f" x vol{int(target * 100)}"

                def rule(d, b=b, m=m, target=target):  # type: ignore[no-untyped-def]
                    w = hold_on((d["btc"] > sma(d["btc"], b)) & (d["close"] > shift(d["close"], m)))
                    return (
                        w if target is None else w * np.minimum(1.0, target / vol(d["close"], 30))
                    )

                out[f"btc>sma{b} & coin up {m}d{tag}"] = ("btc filter", rule)
    for f in (5, 10, 15, 20):
        for s in (30, 40, 50, 75, 100):
            if f >= s:
                continue
            out[f"sma {f}/{s}"] = (
                "crossover",
                lambda d, f=f, s=s: hold_on(sma(d["close"], f) > sma(d["close"], s)),
            )
            out[f"sma {f}/{s} & btc>sma50"] = (
                "crossover",
                lambda d, f=f, s=s: hold_on(
                    (sma(d["close"], f) > sma(d["close"], s)) & (d["btc"] > sma(d["btc"], 50))
                ),
            )
    for target in (0.3, 0.4, 0.5, 0.7, 1.0):
        for n in (20, 30, 60, 90):
            out[f"vol target {int(target * 100)}% n={n}"] = (
                "vol target",
                lambda d, t=target, n=n: np.minimum(1.0, t / vol(d["close"], n)),
            )
    for drop in (0.2, 0.25, 0.3, 0.35, 0.4):
        for days in (3, 5, 7, 14):
            for hold in (3, 7, 14):

                def crash(d, drop=drop, days=days, hold=hold):  # type: ignore[no-untyped-def]
                    hit = (d["close"] / shift(d["close"], days) - 1 < -drop).astype(float)
                    return (pl.Series(hit).rolling_max(hold, min_samples=1).to_numpy() > 0).astype(
                        float
                    )

                out[f"crash -{int(drop * 100)}%/{days}d hold {hold}d"] = ("crash rebound", crash)
    return out


def main() -> None:
    coins, _ = load()
    rows = [{"rule": k, "family": fam, **score(coins, rule)} for k, (fam, rule) in grid().items()]
    table = pl.DataFrame(rows)
    out = ROOT / "data" / "exploration" / "round2.csv"
    table.write_csv(out)
    with pl.Config(tbl_rows=40, tbl_width_chars=220, fmt_str_lengths=40):
        for fam in table["family"].unique(maintain_order=True):
            best = table.filter(pl.col("family") == fam).sort("both_halves", descending=True)
            print(f"\n== {fam}: {best.height} rules; best 6 by both-halves share")
            print(best.head(6).drop("family", "coins"))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
