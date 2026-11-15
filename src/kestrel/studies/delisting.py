"""What happens between a Binance delisting announcement and the coin's removal?

A raw-effect study, pre-registered in research/strategies/what-happens-between-a-binance-
delisting. For each delisted coin (kestrel.data.delistings) on daily UTC bars:

- day 0 is the UTC day of the announcement, which comes out during it, so the earliest
  realistic trade is day 1's open;
- day R is the UTC day trading stops (03:00 or 09:00 UTC); its open (00:00 UTC) is the
  last price a holder can surely trade at.

Log returns per coin: the pre-drift (close of day -31 to close of day -1), the
announcement day (close of day -1 to close of day 0), and the window (open of day 1 to
open of day R), each also net of BTCUSDT over the same span. The trade the study tests is
the window: buy at day 1's open, sell at day R's open.

Coins announced together share a date and a market, so they are not independent: each
announcement's coins are averaged first and the announcement is the unit of the bootstrap.

Costs: a round trip of the taker fee and the coin's half-spread band (kestrel.backtest.
spread) known at day 0's close. A result must survive twice that (scorecard rule 5).
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from kestrel.backtest.spread import with_half_spread
from kestrel.config import Config
from kestrel.data import delistings
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.data.universe import NOT_COINS

CONFIDENCE = 0.90
PRE_DAYS = 30
NOT_COINS_EXTRA = frozenset({"VAI", "IDRT"})
"""Venus's VAI (a stablecoin) and IDRT (the rupiah) aren't in the universe's list."""


@dataclass(frozen=True)
class CoinPath:
    coin: str
    article: str
    announced: date
    removal: date
    pre: float | None
    announcement_day: float | None
    window: float
    pre_vs_btc: float | None
    announcement_day_vs_btc: float | None
    window_vs_btc: float
    round_trip: float
    """The round trip's cost as a fraction: 2 x (taker fee + half-spread)."""
    flag: str | None = None


@dataclass(frozen=True)
class Event:
    """One announcement: the mean of its coins' figures."""

    article: str
    announced: date
    coins: tuple[str, ...]
    pre: float | None
    announcement_day: float | None
    window: float
    window_vs_btc: float
    round_trip: float


def _mean(xs: Sequence[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


def _by_day(frame: pl.DataFrame) -> dict[date, dict[str, float]]:
    return {
        row["open_time"].date(): {"open": float(row["open"]), "close": float(row["close"])}
        for row in frame.iter_rows(named=True)
    }


def coin_path(
    coin: str,
    article: str,
    announced_at: datetime,
    removal_at: datetime,
    bars: pl.DataFrame,
    btc: pl.DataFrame,
    *,
    taker_fee: float,
    tiers: Sequence[tuple[float, float]],
    volume_window: int,
) -> CoinPath | str:
    """One coin's path, or why it can't be measured (reported, never filled)."""
    day0, removal = announced_at.date(), removal_at.date()
    start = datetime.combine(removal, datetime.min.time(), tzinfo=removal_at.tzinfo)
    if removal_at <= start:
        return f"{coin}: removal at {removal_at} leaves no tradable open on its day"
    if removal <= day0 + timedelta(days=1):
        return f"{coin}: removal on {removal} leaves no day 1 to trade"
    if bars.is_empty():
        return f"{coin}: no USDT daily bars"
    banded = with_half_spread(
        bars.sort("open_time").with_columns(_coin=pl.lit(0)), volume_window, tiers, ["_coin"]
    )
    days, market = _by_day(banded), _by_day(btc)
    spread = banded.filter(pl.col("open_time").dt.date() == day0)["half_spread"]
    if day0 not in days or (day0 + timedelta(days=1)) not in days:
        return f"{coin}: no bar on day 0 or day 1 ({day0})"
    entry = days[day0 + timedelta(days=1)]["open"]
    flag = None
    if removal in days:
        exit_price, exit_day, exit_field = days[removal]["open"], removal, "open"
    else:
        last = max(d for d in days if d < removal)
        exit_price, exit_day, exit_field = days[last]["close"], last, "close"
        flag = f"bars stop on {last}, before removal on {removal}: exit at its close"

    def log(a: float | None, b: float | None) -> float | None:
        return math.log(b / a) if a and b else None

    def close(d: date, src: dict[date, dict[str, float]]) -> float | None:
        return src[d]["close"] if d in src else None

    def btc_window() -> float | None:
        a = market.get(day0 + timedelta(days=1), {}).get("open")
        b = market.get(exit_day, {}).get(exit_field)
        return log(a, b)

    window = math.log(exit_price / entry)
    btc_w = btc_window()
    if btc_w is None:
        return f"{coin}: no BTCUSDT bar for the window"
    pre = log(
        close(day0 - timedelta(days=PRE_DAYS + 1), days), close(day0 - timedelta(days=1), days)
    )
    pre_btc = log(
        close(day0 - timedelta(days=PRE_DAYS + 1), market), close(day0 - timedelta(days=1), market)
    )
    ann = log(close(day0 - timedelta(days=1), days), close(day0, days))
    ann_btc = log(close(day0 - timedelta(days=1), market), close(day0, market))
    return CoinPath(
        coin=coin,
        article=article,
        announced=day0,
        removal=removal,
        pre=pre,
        announcement_day=ann,
        window=window,
        pre_vs_btc=None if pre is None or pre_btc is None else pre - pre_btc,
        announcement_day_vs_btc=None if ann is None or ann_btc is None else ann - ann_btc,
        window_vs_btc=window - btc_w,
        round_trip=2 * (taker_fee + float(spread[0])),
        flag=flag,
    )


def measure_all(data_dir: Path | str, cfg: Config) -> tuple[list[CoinPath], list[str]]:
    """Every delisted coin removed before the holdout, measured on its USDT spot bars, and
    why each one that couldn't be measured was left out."""
    holdout = cfg.backtest.holdout_start
    bars_store = ParquetStore(data_dir)
    found = delistings.load(data_dir).filter(pl.col("removal_at") < holdout)
    tiers = [(float(v), float(bps) / 1e4) for v, bps in cfg.universe.spread_tiers]
    lead = timedelta(days=PRE_DAYS + cfg.universe.volume_window_days + 5)
    paths: list[CoinPath] = []
    problems: list[str] = []
    for row in found.iter_rows(named=True):
        coin = row["coin"]
        if coin in NOT_COINS or coin in NOT_COINS_EXTRA:
            problems.append(f"{coin}: a stablecoin or fiat token, left out")
            continue
        start = row["announced_at"] - lead
        end = min(row["removal_at"] + timedelta(days=1), holdout)
        measured = coin_path(
            coin,
            row["article"],
            row["announced_at"],
            row["removal_at"],
            bars_store.read(f"{coin}USDT", Interval.D1, start, end),
            bars_store.read("BTCUSDT", Interval.D1, start, end),
            taker_fee=float(cfg.costs.taker_fee_bps) / 1e4,
            tiers=tiers,
            volume_window=cfg.universe.volume_window_days,
        )
        if isinstance(measured, str):
            problems.append(measured)
        else:
            paths.append(measured)
    return paths, problems


def events(paths: list[CoinPath]) -> list[Event]:
    grouped: dict[str, list[CoinPath]] = {}
    for p in paths:
        grouped.setdefault(p.article, []).append(p)
    out = []
    for article, ps in grouped.items():
        n = len(ps)
        window = sum(p.window for p in ps) / n
        adj = sum(p.window_vs_btc for p in ps) / n
        cost = sum(p.round_trip for p in ps) / n
        out.append(
            Event(
                article=article,
                announced=ps[0].announced,
                coins=tuple(p.coin for p in ps),
                pre=_mean([p.pre_vs_btc for p in ps]),
                announcement_day=_mean([p.announcement_day_vs_btc for p in ps]),
                window=window,
                window_vs_btc=adj,
                round_trip=cost,
            )
        )
    return sorted(out, key=lambda e: e.announced)


def share_above_zero(values: list[float], resamples: int, seed: int) -> float:
    """Bootstrap share of resampled means above zero."""
    rng = random.Random(seed)
    n = len(values)
    return sum(sum(rng.choice(values) for _ in range(n)) / n > 0 for _ in range(resamples)) / (
        resamples
    )


@dataclass(frozen=True)
class DelistingResult:
    paths: list[CoinPath]
    events: list[Event]
    problems: list[str]
    mean_window: float
    mean_window_vs_btc: float
    mean_round_trip: float
    mean_net_double: float
    """Mean over announcements of the window less twice the round trip."""
    p_net_double_positive: float
    p_window_negative: float
    mean_net_triple: float

    @property
    def verdict(self) -> str:
        hurdle = 2 * self.mean_round_trip
        if (
            self.mean_net_double > 0
            and self.p_net_double_positive >= CONFIDENCE
            and self.mean_window_vs_btc > 0
        ):
            return "REBOUND"
        if self.mean_window < -hurdle and self.p_window_negative >= CONFIDENCE:
            return "EXIT RULE"
        return "KILL"


def run_delisting_study(
    paths: list[CoinPath], problems: list[str], *, resamples: int = 5000, seed: int = 0
) -> DelistingResult:
    found = events(paths)
    if not found:
        raise ValueError("no announcement could be measured")
    net_double = [e.window - 2 * e.round_trip for e in found]
    net_triple = [e.window - 3 * e.round_trip for e in found]
    windows = [e.window for e in found]
    n = len(found)
    return DelistingResult(
        paths=paths,
        events=found,
        problems=problems,
        mean_window=sum(windows) / n,
        mean_window_vs_btc=sum(e.window_vs_btc for e in found) / n,
        mean_round_trip=sum(e.round_trip for e in found) / n,
        mean_net_double=sum(net_double) / n,
        p_net_double_positive=share_above_zero(net_double, resamples, seed),
        p_window_negative=share_above_zero([-w for w in windows], resamples, seed),
        mean_net_triple=sum(net_triple) / n,
    )


def format_delisting_study(r: DelistingResult) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{math.expm1(x):+.2%}"

    flagged = [p for p in r.paths if p.flag]
    lines = [
        "Delisting study: Binance spot, announcement to removal, daily bars, before the holdout",
        f"{len(r.paths)} coins in {len(r.events)} announcements"
        + (f"; {len(r.problems)} coins not measured (below)" if r.problems else ""),
        "",
        "Per announcement (its coins averaged; vs BTC = net of BTCUSDT over the same span):",
        f"  {'announced':<11}{'coins':>6}{'pre 30d':>10}{'day 0':>9}{'window':>9}{'vs BTC':>9}",
    ]
    for e in r.events:
        lines.append(
            f"  {e.announced!s:<11}{len(e.coins):>6}{pct(e.pre):>10}{pct(e.announcement_day):>9}"
            f"{pct(e.window):>9}{pct(e.window_vs_btc):>9}"
        )
    lines += [
        "",
        "Means over announcements:",
        f"  pre-drift vs BTC {pct(_mean([e.pre for e in r.events]))}, "
        f"announcement day vs BTC {pct(_mean([e.announcement_day for e in r.events]))} "
        "(information only)",
        f"  window (buy day 1's open, sell day R's open): {pct(r.mean_window)}, "
        f"vs BTC {pct(r.mean_window_vs_btc)}",
        f"  round trip {pct(r.mean_round_trip)}; window less 2x cost {pct(r.mean_net_double)} "
        f"(P > 0: {r.p_net_double_positive:.0%}); less 3x cost {pct(r.mean_net_triple)}",
        f"  P(window < 0): {r.p_window_negative:.0%}; the short side's window, information "
        f"only (long-only can't take it): {-math.expm1(r.mean_window):+.2%} before borrowing",
        "",
        f"Verdict: {r.verdict}",
    ]
    if flagged:
        lines += ["", "Flagged (bars stop before removal):"]
        lines += [f"  {p.coin}: {p.flag}" for p in flagged]
    if r.problems:
        lines += ["", "Not measured:"] + [f"  {p}" for p in r.problems]
    return "\n".join(lines)
