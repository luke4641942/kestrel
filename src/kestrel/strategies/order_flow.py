"""Order flow Model 2 (research, YouTube batch 2 C, Fabio Valentino), long only.

A failed breakdown below the previous UTC day's value area, reclaimed by a bar where
buyers took most of the volume, bought back toward the point of control. Each rule is
fixed here (``signals``) so nothing is decided by eye; the cost gate
(kestrel.studies.cost_gate) measures the same setups' stops, from this code.

Delta is each bar's taker-buy share of its volume: Binance's own split of a bar's
volume into aggressive buys and sells, a stand-in for trade-level data.

As a strategy: entry at the next open after the signal; stop at the breakdown's lowest
low; target the point of control. An intraday method: a trade still open is sold at the
next open after the day's last bar (the first open of the next UTC day). A signal on
the day's last bar is skipped, since its entry would fall on the next day.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import polars as pl

BIN_BPS = 5.0
"""Volume-profile bin width, in bps of the previous day's last close."""
VALUE_AREA = 0.70
RECLAIM_BARS = 15


@dataclass(frozen=True)
class Signal:
    at: datetime
    """Open time of the reclaim bar (the signal, known at its close)."""
    entry: float
    """The reclaim bar's close."""
    stop: float
    target: float


def value_area(
    bars: pl.DataFrame, bin_bps: float = BIN_BPS, share: float = VALUE_AREA
) -> tuple[float, float, float] | None:
    """(value area low, point of control, value area high) of one session's bars.

    Each bar's volume goes to the bin of its typical price, (high + low + close) / 3. The
    point of control is the busiest bin's middle; the value area grows from it, adding
    whichever neighbouring bin traded more, until it holds 70% of the volume."""
    if bars.is_empty() or float(bars["volume"].sum()) <= 0:
        return None
    width = float(bars["close"][-1]) * bin_bps / 1e4
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    bins: dict[int, float] = {}
    for price, vol in zip(typical.to_list(), bars["volume"].to_list(), strict=True):
        key = math.floor(price / width)
        bins[key] = bins.get(key, 0.0) + vol
    total = sum(bins.values())
    poc = max(bins, key=lambda k: (bins[k], -k))
    lo = hi = poc
    held = bins[poc]
    while held < share * total:
        below, above = bins.get(lo - 1, 0.0), bins.get(hi + 1, 0.0)
        if lo - 1 < min(bins) and hi + 1 > max(bins):
            break
        if above >= below and hi + 1 <= max(bins):
            hi += 1
            held += above
        else:
            lo -= 1
            held += below
    return lo * width, (poc + 0.5) * width, (hi + 1) * width


def signals(
    bars: pl.DataFrame,
    bin_bps: float = BIN_BPS,
    share: float = VALUE_AREA,
    reclaim_bars: int = RECLAIM_BARS,
) -> list[Signal]:
    """Every order-flow Model 2 setup in ``bars`` (batch 2 C, Fabio Valentino), long only.

    Fixed before any measurement (2026-11-16). For each UTC day, the previous day's value
    area (``value_area`` with ``bin_bps`` and ``share``). The first bar of the day whose
    low trades below the value area low starts a breakdown; it fails if, within
    ``reclaim_bars`` bars from that bar, one closes back above the value area low with
    positive delta (taker buys more than half the bar's volume: order flow turned to
    buyers). Entry is that close, the stop the lowest low from the breakdown bar to the
    reclaim, the target the point of control (it must be above the entry). One setup a
    day at most. A day whose previous UTC day has no bars has no value area and no setup.
    Only bars up to the reclaim's close are used.
    """
    frame = bars.sort("open_time").with_columns(day=pl.col("open_time").dt.date())
    found: list[Signal] = []
    days = frame.partition_by("day", maintain_order=True)
    for before, today in itertools.pairwise(days):
        if today["day"][0] - before["day"][0] != timedelta(days=1):
            continue  # the previous day is missing: no value area to trade against
        area = value_area(before, bin_bps, share)
        if area is None:
            continue
        val, poc, _ = area
        rows = today.to_dicts()
        start = next((i for i, r in enumerate(rows) if r["low"] < val), None)
        if start is None:
            continue
        for j in range(start, min(start + reclaim_bars, len(rows))):
            r = rows[j]
            buyers = r["taker_buy_base_volume"] * 2 > r["volume"]
            if r["close"] > val and buyers:
                low = min(x["low"] for x in rows[start : j + 1])
                if poc > r["close"] > low:
                    found.append(Signal(r["open_time"], r["close"], low, poc))
                break
    return found


class OrderFlow:
    def __init__(
        self,
        symbol: str = "ETHUSDT",
        bin_bps: int = 5,
        value_area_pct: int = 70,
        reclaim_bars: int = 15,
    ) -> None:
        self.symbol = symbol
        self.bin_bps, self.value_area_pct = bin_bps, value_area_pct
        self.reclaim_bars = reclaim_bars
        self.name = f"order flow Model 2 {symbol}"

    def brackets(self, bars: pl.DataFrame) -> pl.DataFrame:
        n = bars.height
        times = bars["open_time"].to_list()
        step = times[1] - times[0] if n > 1 else None
        last_of_day = [step is not None and (t + step).date() != t.date() for t in times]
        enter = [False] * n
        stop: list[float | None] = [None] * n
        target: list[float | None] = [None] * n
        row = {t: i for i, t in enumerate(times)}
        found = signals(bars, float(self.bin_bps), self.value_area_pct / 100, self.reclaim_bars)
        for sig in found:
            i = row[sig.at]
            if last_of_day[i]:
                continue
            enter[i], stop[i], target[i] = True, sig.stop, sig.target
        return pl.DataFrame(
            {"enter": enter, "stop": stop, "target": target, "exit": last_of_day},
            schema={
                "enter": pl.Boolean,
                "stop": pl.Float64,
                "target": pl.Float64,
                "exit": pl.Boolean,
            },
        )
