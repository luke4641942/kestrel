"""Exploration round 1 (CLAUDE.md "Exploration experiment"): a broad screen of daily rules
on the exploration half only. Nothing here is a trial or evidence; it only finds ideas.

Each rule gives a daily weight in [0, 1] per coin, decided at the close and held from the
next day (a one-day lag, like the engine). Costs: the taker fee plus the coin's spread
band (its 30-day median daily volume, known the day before) on every change in weight.
A rule scores on how many coins its Sharpe beats holding that coin (holding a constant
fraction of a coin has the same Sharpe, so this is scorecard test 1), in each half of each
coin's history separately, and by the median Sharpe gap.

Run: .venv/bin/python research/exploration/round1.py   (writes data/exploration/round1.csv)
"""

from __future__ import annotations

import itertools
import math
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from kestrel.config import load_config
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore

ROOT = Path(__file__).resolve().parents[2]
DAYS = 365
Rule = Callable[[dict[str, np.ndarray]], np.ndarray]


def load() -> tuple[dict[str, dict[str, np.ndarray]], dict[str, np.ndarray]]:
    cfg = load_config(ROOT / "kestrel.toml")
    split = tomllib.loads((ROOT / "research/exploration/split.toml").read_text())
    store = ParquetStore(ROOT / "data")
    end = cfg.backtest.holdout_start
    tiers = [(float(v), float(b) / 1e4) for v, b in cfg.universe.spread_tiers]
    fee = float(cfg.costs.taker_fee_bps) / 1e4
    btc = store.read("BTCUSDT", Interval.D1, None, end).select("open_time", "close")
    coins: dict[str, dict[str, np.ndarray]] = {}
    for symbol in split["exploration"]:
        bars = store.read(symbol, Interval.D1, None, end)
        if bars.height < 400:
            continue
        bars = bars.join(btc.rename({"close": "btc"}), on="open_time", how="left")
        vol = bars["quote_volume"].to_numpy()
        median = (
            pl.Series(vol).rolling_median(30, min_samples=30).shift(1).to_numpy()
        )  # known the day before
        spread = np.full(len(vol), max(t[1] for t in tiers))
        for floor, hs in reversed(tiers):
            spread = np.where(np.nan_to_num(median, nan=-1) >= floor, hs, spread)
        coins[symbol] = {
            "close": bars["close"].to_numpy(),
            "high": bars["high"].to_numpy(),
            "low": bars["low"].to_numpy(),
            "volume": vol,
            "btc": bars["btc"].to_numpy(),
            "cost": fee + spread,
            "weekday": bars["open_time"].dt.weekday().to_numpy(),
        }
    return coins, {"fee": np.array([fee])}


def shift(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    if n < len(x):
        out[n:] = x[: len(x) - n]
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pl.Series(x).rolling_mean(n, min_samples=n).to_numpy()


def rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    return pl.Series(x).rolling_max(n, min_samples=n).to_numpy()


def rolling_min(x: np.ndarray, n: int) -> np.ndarray:
    return pl.Series(x).rolling_min(n, min_samples=n).to_numpy()


def vol(c: np.ndarray, n: int) -> np.ndarray:
    r = np.diff(np.log(c), prepend=np.nan)
    return pl.Series(r).rolling_std(n, min_samples=n).to_numpy() * math.sqrt(DAYS)


def rsi(c: np.ndarray, n: int) -> np.ndarray:
    d = np.diff(c, prepend=np.nan)
    up = pl.Series(np.where(d > 0, d, 0.0)).ewm_mean(alpha=1 / n, adjust=False).to_numpy()
    down = pl.Series(np.where(d < 0, -d, 0.0)).ewm_mean(alpha=1 / n, adjust=False).to_numpy()
    return 100 - 100 / (1 + up / np.where(down == 0, np.nan, down))


def hold_on(cond: np.ndarray) -> np.ndarray:
    return np.where(np.isnan(cond.astype(float)), 0.0, cond.astype(float))


def stateful(enter: np.ndarray, leave: np.ndarray) -> np.ndarray:
    w = np.zeros(len(enter))
    inside = False
    for i in range(len(enter)):
        if not inside and enter[i]:
            inside = True
        elif inside and leave[i]:
            inside = False
        w[i] = 1.0 if inside else 0.0
    return w


def rules() -> dict[str, tuple[str, Rule]]:
    """name -> (family, rule). Weights decided at each close."""
    out: dict[str, tuple[str, Rule]] = {}
    for n in (5, 10, 20, 40, 60, 120, 200):
        out[f"tsmom {n}d"] = ("trend", lambda d, n=n: hold_on(d["close"] > shift(d["close"], n)))
        out[f"above sma {n}d"] = ("trend", lambda d, n=n: hold_on(d["close"] > sma(d["close"], n)))
    for f, s in ((5, 20), (10, 50), (20, 100), (50, 200)):
        out[f"sma {f}/{s}"] = (
            "trend",
            lambda d, f=f, s=s: hold_on(sma(d["close"], f) > sma(d["close"], s)),
        )
    out["trend votes 20/60/120"] = (
        "trend",
        lambda d: sum(hold_on(d["close"] > shift(d["close"], n)) for n in (20, 60, 120)) / 3,
    )
    for n, m in ((20, 10), (55, 20), (100, 50), (10, 5)):
        out[f"donchian {n}/{m}"] = (
            "breakout",
            lambda d, n=n, m=m: stateful(
                d["close"] > shift(rolling_max(d["high"], n), 1),
                d["close"] < shift(rolling_min(d["low"], m), 1),
            ),
        )
    for lvl, ex in ((10, 70), (20, 60), (30, 50)):
        out[f"rsi2 <{lvl} out >{ex}"] = (
            "reversal",
            lambda d, lvl=lvl, ex=ex: stateful(rsi(d["close"], 2) < lvl, rsi(d["close"], 2) > ex),
        )
    for drop in (0.1, 0.2, 0.3):
        out[f"buy after -{int(drop * 100)}% week, hold 7d"] = (
            "reversal",
            lambda d, drop=drop: hold_on(
                pl.Series((d["close"] / shift(d["close"], 7) - 1 < -drop).astype(float))
                .rolling_max(7, min_samples=1)
                .to_numpy()
                > 0
            ),
        )
    for n in (50, 100, 200):
        out[f"btc above sma {n}d"] = (
            "btc filter",
            lambda d, n=n: hold_on(d["btc"] > sma(d["btc"], n)),
        )
        out[f"btc>sma{n} and coin tsmom 20"] = (
            "btc filter",
            lambda d, n=n: hold_on(
                (d["btc"] > sma(d["btc"], n)) & (d["close"] > shift(d["close"], 20))
            ),
        )
    for n in (10, 20, 40, 60, 120):
        out[f"beats btc over {n}d"] = (
            "relative strength",
            lambda d, n=n: hold_on(
                (d["close"] / shift(d["close"], n)) > (d["btc"] / shift(d["btc"], n))
            ),
        )
        out[f"beats btc {n}d and rising"] = (
            "relative strength",
            lambda d, n=n: hold_on(
                ((d["close"] / shift(d["close"], n)) > (d["btc"] / shift(d["btc"], n)))
                & (d["close"] > shift(d["close"], n))
            ),
        )
    for k in (2, 3, 5):
        out[f"volume {k}x and up day, hold 5d"] = (
            "volume",
            lambda d, k=k: hold_on(
                pl.Series(
                    (
                        (d["volume"] > k * shift(sma(d["volume"], 20), 1))
                        & (d["close"] > shift(d["close"], 1))
                    ).astype(float)
                )
                .rolling_max(5, min_samples=1)
                .to_numpy()
                > 0
            ),
        )
    for n in (30, 90):
        out[f"vol below its {n}d-ago median"] = (
            "volatility",
            lambda d, n=n: hold_on(
                vol(d["close"], 20)
                < pl.Series(vol(d["close"], 20)).rolling_median(n * 2, min_samples=n).to_numpy()
            ),
        )
        out[f"vol target 50% (n={n})"] = (
            "volatility",
            lambda d, n=n: np.nan_to_num(np.minimum(1.0, 0.5 / vol(d["close"], n)), nan=0.0),
        )
    out["trend votes x vol target"] = (
        "trend",
        lambda d: np.nan_to_num(
            sum(hold_on(d["close"] > shift(d["close"], n)) for n in (20, 60, 120))
            / 3
            * np.minimum(1.0, 0.5 / vol(d["close"], 30)),
            nan=0.0,
        ),
    )
    for day in range(7):
        out[f"weekday {day} only"] = (
            "calendar",
            lambda d, day=day: (d["weekday"] == day + 1).astype(float),
        )
    return out


def sharpe(r: np.ndarray) -> float | None:
    r = r[~np.isnan(r)]
    if len(r) < 60 or r.std() == 0:
        return None
    return float(r.mean() / r.std() * math.sqrt(DAYS))


def evaluate(d: dict[str, np.ndarray], w: np.ndarray) -> dict[str, float | None]:
    ret = np.diff(d["close"], prepend=np.nan) / shift(d["close"], 1)
    held = shift(w, 1)  # decided at the close, held the next day
    turnover = np.abs(np.diff(np.nan_to_num(w), prepend=0.0))
    strat = held * ret - shift(turnover * d["cost"], 1)
    half = len(ret) // 2
    out: dict[str, float | None] = {}
    for tag, sl in (("all", slice(None)), ("first", slice(0, half)), ("second", slice(half, None))):
        s, h = sharpe(strat[sl]), sharpe(ret[sl])
        out[f"gap_{tag}"] = None if s is None or h is None else s - h
    out["exposure"] = float(np.nanmean(held))
    out["turnover"] = float(np.nansum(turnover) / len(ret) * DAYS)
    return out


def main() -> None:
    coins, _ = load()
    print(f"{len(coins)} exploration coins with 400+ days")
    rows = []
    for name, (family, rule) in rules().items():
        gaps: dict[str, list[float]] = {"all": [], "first": [], "second": []}
        both, exposure, turnover = 0, [], []
        for d in coins.values():
            e = evaluate(d, rule(d))
            for tag in gaps:
                if e[f"gap_{tag}"] is not None:
                    gaps[tag].append(e[f"gap_{tag}"])  # type: ignore[arg-type]
            if (e["gap_first"] or -1) > 0 and (e["gap_second"] or -1) > 0:
                both += 1
            exposure.append(e["exposure"])
            turnover.append(e["turnover"])
        n = len(gaps["all"])
        rows.append(
            {
                "rule": name,
                "family": family,
                "coins": n,
                "beats_hold_share": sum(g > 0 for g in gaps["all"]) / n,
                "both_halves_share": both / n,
                "median_gap": float(np.median(gaps["all"])),
                "median_gap_first": float(np.median(gaps["first"])),
                "median_gap_second": float(np.median(gaps["second"])),
                "exposure": float(np.mean(exposure)),
                "turnover": float(np.mean(turnover)),
            }
        )
    table = pl.DataFrame(rows).sort("both_halves_share", descending=True)
    out = ROOT / "data" / "exploration" / "round1.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    table.write_csv(out)
    with pl.Config(tbl_rows=80, tbl_width_chars=200, fmt_str_lengths=40):
        print(
            table.select(
                "rule",
                "family",
                "beats_hold_share",
                "both_halves_share",
                "median_gap",
                "median_gap_first",
                "median_gap_second",
                "exposure",
                "turnover",
            )
        )
    print(f"-> {out}  ({datetime.now(UTC):%Y-%m-%d %H:%M} UTC)")


if __name__ == "__main__":
    _ = itertools
    main()
