"""The cost gate: can a minute-chart setup's stop carry a round trip's costs?

Luke's YouTube strategies (2026-11-15) trade 1m and 5m charts with stops just beyond a
fair value gap. Costs are a fixed share of the trade's size, and risk is the distance to
the stop, so the cost of a trade in R is

    cost in R = round-trip cost (bps) / stop distance (bps),

whatever the position size. A 22 bps round trip against a 30 bps stop is 0.73R a trade,
paid on wins and losses alike. At a target of T R, the win rate needed to break even rises
from 1 / (T + 1) to (1 + c) / (T + 1) for a cost of c R.

This measures the stops only, never what happened after: no returns, no trades, so no
trial. The setup is the batch-1 spec's S2 (Craig Percoco), long only:

1. Swing highs with ``swing_bars`` bars either side, known ``swing_bars`` bars later (as
   kestrel.strategies.structure).
2. A bullish change of character: the last known swing high is below the one before it,
   and a bar closes above it. One per swing high.
3. The leg runs from the lowest low since that swing high to the change of character.
4. The leg's most recent bullish fair value gap (third bar's low above the first bar's
   high) at least ``min_gap_atr`` x ATR(14) wide, the bar after the change of character
   included (the breaking bar's push often leaves the gap). Entry is its midpoint, the
   stop the first bar's low. A change of character with no such gap has no setup. Using
   one bar past the break is harmless here: nothing is traded or scored on outcomes.

S3's 5m setups place their stops the same way (beyond the shift's zone), so the same
measurement runs on 5m bars as a stand-in for S3.

Verdict, fixed before any measurement (2026-11-15), on the median setup's cost in R:
CARRIES at or below 0.25R (the break-even win rate at 4R rises from 20% to no more than
25%); FAILS above 0.5R (above 30%); MARGINAL between. A gate that fails is a measured
reason not to build the strategy; dropping it is still Luke's call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from statistics import median
from typing import Any

import polars as pl

from kestrel.atomic import write_atomically
from kestrel.indicators import atr
from kestrel.strategies import order_flow

CARRIES_AT = 0.25
FAILS_ABOVE = 0.5
TARGETS_R = (2, 4)
NY_OPEN = (time(13, 0), time(16, 0))
"""UTC hours covering New York's 9:30 open in both daylight-saving states (13:30 or 14:30
UTC) and the hour or two after it, which S2 trades."""


@dataclass(frozen=True)
class Setup:
    at: datetime
    """Open time of the bar whose close made the change of character."""
    entry: float
    stop: float
    target: float | None = None
    """Set when the setup names its own target (order flow: the point of control)."""

    @property
    def width_bps(self) -> float:
        return (self.entry - self.stop) / self.entry * 1e4

    @property
    def target_r(self) -> float | None:
        if self.target is None or self.entry <= self.stop:
            return None
        return (self.target - self.entry) / (self.entry - self.stop)


def setups(
    bars: pl.DataFrame, swing_bars: int = 3, min_gap_atr: float = 0.3, atr_bars: int = 14
) -> list[Setup]:
    """Every S2 setup in ``bars`` (swings and breaks known at each close; the gap may use
    the next bar, see rule 4)."""
    high, low, close = bars["high"].to_list(), bars["low"].to_list(), bars["close"].to_list()
    times = bars["open_time"].to_list()
    ranges = atr(bars, atr_bars).to_list()
    k = swing_bars
    swing_highs: list[tuple[int, float]] = []
    used: int | None = None
    out: list[Setup] = []
    for t in range(len(close)):
        j = t - k
        if j >= k and high[j] > max(high[j - k : j]) and high[j] >= max(high[j + 1 : t + 1]):
            swing_highs.append((j, high[j]))
        if len(swing_highs) < 2:
            continue
        (_, before), (last_at, last) = swing_highs[-2], swing_highs[-1]
        if not (last < before and close[t] > last and used != last_at):
            continue
        used = last_at
        leg_low_at = min(range(last_at, t + 1), key=lambda i: low[i])
        # The breaking bar's own push often leaves the gap, completed by the next bar.
        newest = min(t + 1, len(close) - 1)
        for i in range(newest, max(leg_low_at + 2, 2) - 1, -1):  # the most recent gap first
            gap = low[i] - high[i - 2]
            size = ranges[i]
            if gap > 0 and size is not None and gap >= min_gap_atr * size:
                out.append(Setup(times[t], (high[i - 2] + low[i]) / 2, low[i - 2]))
                break
    return out


def order_flow_setups(
    bars: pl.DataFrame,
    bin_bps: float = order_flow.BIN_BPS,
    share: float = order_flow.VALUE_AREA,
    reclaim_bars: int = order_flow.RECLAIM_BARS,
) -> list[Setup]:
    """Order flow Model 2's setups (kestrel.strategies.order_flow.signals), as stops to
    measure: entry at the reclaim's close, stop at the breakdown's low, target the point
    of control. Fixed before any measurement (2026-11-16)."""
    return [
        Setup(s.at, s.entry, s.stop, s.target)
        for s in order_flow.signals(bars, bin_bps, share, reclaim_bars)
    ]


def break_even_win_rate(target_r: float, cost_r: float) -> float:
    """Win rate at which a +target_r / -1R bracket nets zero after ``cost_r`` a trade."""
    return (1 + cost_r) / (target_r + 1)


@dataclass(frozen=True)
class Gate:
    market: str
    """e.g. "BTCUSDT 1m, NY open"."""
    round_trip_bps: float
    widths_bps: list[float]
    target_rs: list[float] | None = None
    """Each setup's own target in R, when the setup names one (order flow)."""

    @property
    def median_target_r(self) -> float | None:
        return median(self.target_rs) if self.target_rs else None

    @property
    def count(self) -> int:
        return len(self.widths_bps)

    def quantile(self, q: float) -> float | None:
        if not self.widths_bps:
            return None
        ordered = sorted(self.widths_bps)
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    @property
    def median_width(self) -> float | None:
        return median(self.widths_bps) if self.widths_bps else None

    @property
    def median_cost_r(self) -> float | None:
        width = self.median_width
        return None if not width else self.round_trip_bps / width

    @property
    def share_over_half_r(self) -> float | None:
        if not self.widths_bps:
            return None
        return sum(self.round_trip_bps / w > FAILS_ABOVE for w in self.widths_bps) / self.count

    @property
    def verdict(self) -> str:
        cost = self.median_cost_r
        if cost is None:
            return "NO SETUPS"
        return "CARRIES" if cost <= CARRIES_AT else "FAILS" if cost > FAILS_ABOVE else "MARGINAL"


def in_session(at: datetime, session: tuple[time, time] = NY_OPEN) -> bool:
    return session[0] <= at.time() < session[1]


def measure(market: str, found: list[Setup], round_trip_bps: float, ny_only: bool = False) -> Gate:
    chosen = [s for s in found if in_session(s.at)] if ny_only else found
    label = f"{market}, NY open" if ny_only else f"{market}, all hours"
    kept = [s for s in chosen if s.width_bps > 0]
    targets = [t for s in kept if (t := s.target_r) is not None]
    return Gate(label, round_trip_bps, [s.width_bps for s in kept], targets or None)


TITLES = {
    "fvg": "S2 stops (change of character + fair value gap)",
    "order-flow": "order flow Model 2 stops (failed breakdown back to the point of control)",
}


def format_gates(gates: list[Gate], setup: str = "fvg") -> str:
    def num(x: float | None, spec: str) -> str:
        return "n/a" if x is None else format(x, spec)

    rt = gates[0].round_trip_bps if gates else 0.0
    lines = [
        f"Cost gate  ·  {TITLES[setup]}  ·  {rt:.0f} bp round trip",
        "",
        f"  {'market':<34}{'setups':>7}{'p25':>7}{'median':>8}{'p75':>7}"
        f"{'cost':>8}{'>0.5R':>7}{'win% 2R':>9}{'win% 4R':>9}  verdict",
    ]
    for g in gates:
        cost = g.median_cost_r
        rates = [None if cost is None else break_even_win_rate(t, cost) for t in TARGETS_R]
        wins = ["never" if w is not None and w >= 1 else num(w, ".0%") for w in rates]
        lines.append(
            f"  {g.market:<34}{g.count:>7}{num(g.quantile(0.25), '.0f'):>7}"
            f"{num(g.median_width, '.0f'):>8}{num(g.quantile(0.75), '.0f'):>7}"
            f"{num(cost, '.2f') + 'R':>8}{num(g.share_over_half_r, '.0%'):>7}"
            f"{wins[0]:>9}{wins[1]:>9}  {g.verdict}"
        )
    lines += [
        "",
        "  Stop widths in bps from entry. Cost: the median setup's round trip in R. >0.5R: share",
        "  of setups paying more than half a loss in costs. win%: the win rate needed to break",
        "  even after costs (20% at 4R and 33% at 2R before costs); never: not even winning",
        "  every trade would cover them.",
        f"  Verdict on the median: CARRIES <= {CARRIES_AT}R, FAILS > {FAILS_ABOVE}R, "
        "else MARGINAL.",
        "  Stops only, no outcomes: not a trial.",
    ]
    own = [g for g in gates if g.median_target_r is not None]
    if own:
        lines += ["", "  The setup's own target (the point of control), median, in R:"]
        for g in own:
            t, cost = g.median_target_r, g.median_cost_r
            assert t is not None
            need = None if cost is None else break_even_win_rate(t, cost)
            shown = "never" if need is not None and need >= 1 else num(need, ".0%")
            lines.append(f"  {g.market:<34}{t:>6.2f}R   break-even win rate {shown}")
    return "\n".join(lines)


def path(data_dir: Path | str, interval: str, setup: str = "fvg") -> Path:
    name = interval if setup == "fvg" else f"{setup}-{interval}"
    return Path(data_dir) / "cost_gate" / f"{name}.json"


def save(
    gates: list[Gate],
    interval: str,
    first: datetime,
    last: datetime,
    data_dir: Path | str,
    setup: str = "fvg",
) -> Path:
    """The latest gate for a bar size, for the dashboard. A rerun replaces it: the setup
    and the verdict are fixed, so only new data can change it, and outcomes never enter."""
    rows: list[dict[str, Any]] = []
    for g in gates:
        cost = g.median_cost_r
        rows.append(
            {
                "market": g.market,
                "setups": g.count,
                "p25_bps": g.quantile(0.25),
                "median_bps": g.median_width,
                "p75_bps": g.quantile(0.75),
                "cost_r": cost,
                "share_over_half_r": g.share_over_half_r,
                "break_even": {
                    str(t): None if cost is None else break_even_win_rate(t, cost)
                    for t in TARGETS_R
                },
                "verdict": g.verdict,
                "target_r": g.median_target_r,
            }
        )
    doc = {
        "interval": interval,
        "setup": setup,
        "measured_at": datetime.now(UTC).isoformat(),
        "first": first.isoformat(),
        "last": last.isoformat(),
        "round_trip_bps": gates[0].round_trip_bps if gates else None,
        "carries_at": CARRIES_AT,
        "fails_above": FAILS_ABOVE,
        "rows": rows,
    }
    target = path(data_dir, interval, setup)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(target, lambda tmp: tmp.write_text(json.dumps(doc, indent=2)))
    return target


def load_all(data_dir: Path | str) -> list[dict[str, Any]]:
    """Every saved gate, shortest bars first."""
    found = [json.loads(p.read_text()) for p in (Path(data_dir) / "cost_gate").glob("*.json")]
    order = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"]
    return sorted(found, key=lambda d: order.index(d["interval"]) if d["interval"] in order else 99)
