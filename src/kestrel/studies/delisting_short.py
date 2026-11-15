"""Hypothetical: a short in a delisted coin's USDT-M perpetual over the delisting window.

Pre-registered in research/strategies/shorting-the-delisting-window-with-perps. Kestrel
can't short (P5 parked); the answer only decides how much priority derivatives deserve.

The coins are those the spot study measured (kestrel.studies.delisting.measure_all), with
the same day 0, day 1 and removal day R. A coin counts only if its perp (XUSDT, or
1000XUSDT) traded on day 1 and on the exit day: a daily bar with quote volume above zero
on both. Binance's archive keeps publishing files for some contracts long after they
stopped trading, so a file alone doesn't prove a market.

Per coin, on notional: short return = (entry - exit) / entry, entry at the perp's day-1
open, exit at its day-R open (or, with no bar on day R, the last close before it,
flagged). Funding: every settlement after entry and up to exit; a positive rate pays the
short. At 2x leverage, a daily high from day 1 to the day before exit at 1.45 x entry or
more liquidates the trade: -45% of notional, funding still counted. Also at 1x (1.95 x,
-95%). Costs: Binance's base USDT-M taker fee and the coin's spot half-spread band, as a
round trip C; the test charges 2C.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
import polars as pl

from kestrel.data import funding
from kestrel.data.binance_vision import UM_KLINES_URL, BinanceVisionClient, Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore
from kestrel.studies.delisting import CoinPath, share_above_zero

PERP_VENUE = "binance-um"
"""Perp bars are stored apart from spot: data/klines/venue=binance-um/."""

PERP_TAKER_FEE = 5e-4
"""Binance's base USDT-M taker fee, 0.05% a side: the note's assumption (kestrel.toml is spot)."""
MIN_EVENTS = 10
CONFIDENCE = 0.90
LEVERAGE = {2: (1.45, -0.45), 1: (1.95, -0.95)}
"""Leverage: (liquidation price as a multiple of entry, the loss booked on notional)."""


@dataclass(frozen=True)
class ShortPath:
    coin: str
    article: str
    announced: date
    perp: str
    short_return: float
    """(entry - exit) / entry, before any liquidation."""
    funding: float
    """Funding received by the short over the window (negative: paid)."""
    worst_rise: float
    """The highest daily high from day 1 to the day before exit, over entry, less one."""
    discount: float | None
    """The perp's day-1 open against spot's, less one (negative: the perp was cheaper)."""
    round_trip: float
    flag: str | None = None

    def net(self, leverage: int) -> float:
        """Short return (or the liquidation loss) plus funding, before costs."""
        trigger, loss = LEVERAGE[leverage]
        made = loss if 1 + self.worst_rise >= trigger else self.short_return
        return made + self.funding

    def liquidated(self, leverage: int) -> bool:
        return 1 + self.worst_rise >= LEVERAGE[leverage][0]


def _midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def short_path(
    spot: CoinPath,
    perp_symbol: str,
    perp: pl.DataFrame,
    funding: pl.DataFrame | None,
    spot_day1_open: float | None,
    *,
    spot_fee: float,
) -> ShortPath | str:
    """One coin's hypothetical short, or why it doesn't count (reported, never filled)."""
    scale = 1000.0 if perp_symbol.startswith("1000") else 1.0
    days = {row["open_time"].date(): row for row in perp.sort("open_time").iter_rows(named=True)}
    day1 = spot.announced + timedelta(days=1)
    first = days.get(day1)
    if first is None or not first["quote_volume"] > 0:
        return f"{spot.coin}: {perp_symbol} didn't trade on day 1 ({day1})"
    flag = None
    if spot.removal in days:
        exit_day, exit_price, exit_at = spot.removal, days[spot.removal]["open"], "open"
    else:
        exit_day = max(d for d in days if d < spot.removal)
        exit_price, exit_at = days[exit_day]["close"], "close"
        flag = f"perp bars stop on {exit_day}, before removal on {spot.removal}: exit at its close"
    if not days[exit_day]["quote_volume"] > 0:
        return f"{spot.coin}: {perp_symbol} didn't trade on the exit day ({exit_day})"
    entry = float(first["open"])
    held = [d for d in days if day1 <= d < exit_day or (exit_at == "close" and d == exit_day)]
    worst = max(float(days[d]["high"]) for d in held) / entry - 1 if held else 0.0
    start = _midnight(day1)
    end = _midnight(exit_day) + (timedelta(days=1) if exit_at == "close" else timedelta(0))
    received = 0.0
    if funding is None or funding.is_empty():
        flag = (flag + "; " if flag else "") + "no funding history: counted as zero"
    else:
        settled = funding.filter((pl.col("time") > start) & (pl.col("time") <= end))
        received = float(settled["rate"].sum())
        if settled.is_empty():
            flag = (flag + "; " if flag else "") + "no funding settlements in the window"
    half_spread = spot.round_trip / 2 - spot_fee
    return ShortPath(
        coin=spot.coin,
        article=spot.article,
        announced=spot.announced,
        perp=perp_symbol,
        short_return=(entry - float(exit_price)) / entry,
        funding=received,
        worst_rise=worst,
        discount=None if not spot_day1_open else entry / scale / spot_day1_open - 1,
        round_trip=2 * (PERP_TAKER_FEE + half_spread),
        flag=flag,
    )


@dataclass(frozen=True)
class ShortEvent:
    article: str
    announced: date
    coins: tuple[str, ...]
    net2: float
    """Mean of (2x net - 2C) over the announcement's coins."""
    net1: float
    short_return: float
    funding: float
    round_trip: float


def short_events(paths: Sequence[ShortPath]) -> list[ShortEvent]:
    grouped: dict[str, list[ShortPath]] = {}
    for p in paths:
        grouped.setdefault(p.article, []).append(p)
    out = []
    for article, ps in grouped.items():
        n = len(ps)
        out.append(
            ShortEvent(
                article=article,
                announced=ps[0].announced,
                coins=tuple(p.coin for p in ps),
                net2=sum(p.net(2) - 2 * p.round_trip for p in ps) / n,
                net1=sum(p.net(1) - 2 * p.round_trip for p in ps) / n,
                short_return=sum(p.short_return for p in ps) / n,
                funding=sum(p.funding for p in ps) / n,
                round_trip=sum(p.round_trip for p in ps) / n,
            )
        )
    return sorted(out, key=lambda e: e.announced)


@dataclass(frozen=True)
class ShortResult:
    paths: list[ShortPath]
    events: list[ShortEvent]
    problems: list[str]
    mean_net2: float | None
    p_net2_positive: float | None
    mean_net1: float | None

    @property
    def verdict(self) -> str:
        if len(self.events) < MIN_EVENTS or self.mean_net2 is None:
            return "INSUFFICIENT"
        assert self.p_net2_positive is not None
        if self.mean_net2 > 0 and self.p_net2_positive >= CONFIDENCE:
            return "PROCEED"
        return "KILL"


def run_short_study(
    paths: list[ShortPath], problems: list[str], *, resamples: int = 5000, seed: int = 0
) -> ShortResult:
    found = short_events(paths)
    if not found:
        return ShortResult(paths, found, problems, None, None, None)
    net2 = [e.net2 for e in found]
    return ShortResult(
        paths=paths,
        events=found,
        problems=problems,
        mean_net2=sum(net2) / len(net2),
        p_net2_positive=share_above_zero(net2, resamples, seed),
        mean_net1=sum(e.net1 for e in found) / len(found),
    )


def format_short_study(r: ShortResult) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:+.2%}"

    def mean(xs: Sequence[float | None]) -> float | None:
        vals = [x for x in xs if x is not None]
        return sum(vals) / len(vals) if vals else None

    lines = [
        "Delisting short study (hypothetical): USDT-M perps, day 1's open to removal, "
        "before the holdout",
        f"{len(r.paths)} coins with a traded perp in {len(r.events)} announcements "
        f"({MIN_EVENTS} needed); {len(r.problems)} coins without one (below)",
        "",
        "Per announcement (on notional; net = short or liquidation + funding - 2x costs):",
        f"  {'announced':<11}{'coins':>6}{'short':>9}{'funding':>9}{'net 2x':>9}{'net 1x':>9}",
    ]
    for e in r.events:
        lines.append(
            f"  {e.announced!s:<11}{len(e.coins):>6}{pct(e.short_return):>9}"
            f"{pct(e.funding):>9}{pct(e.net2):>9}{pct(e.net1):>9}"
        )
    liq2 = sum(p.liquidated(2) for p in r.paths)
    liq1 = sum(p.liquidated(1) for p in r.paths)
    lines += [
        "",
        "Information only:",
        f"  perp vs spot at day 1's open: {pct(mean([p.discount for p in r.paths]))} on average",
        f"  funding received over the window: {pct(mean([p.funding for p in r.paths]))} "
        "on average per coin",
        f"  liquidated: {liq2} of {len(r.paths)} at 2x, {liq1} at 1x; worst rise "
        f"{pct(max((p.worst_rise for p in r.paths), default=None))}",
        f"  round trip {pct(mean([p.round_trip for p in r.paths]))} on average",
        "",
        f"Mean over announcements: net at 2x {pct(r.mean_net2)} "
        f"(P > 0: {'n/a' if r.p_net2_positive is None else f'{r.p_net2_positive:.0%}'}); "
        f"at 1x {pct(r.mean_net1)} (reported, decides nothing)",
        "",
        f"Verdict: {r.verdict}",
    ]
    flagged = [p for p in r.paths if p.flag]
    if flagged:
        lines += ["", "Flagged:"] + [f"  {p.coin}: {p.flag}" for p in flagged]
    if r.problems:
        lines += ["", "Not counted:"] + [f"  {p}" for p in r.problems]
    return "\n".join(lines)


def perp_symbols(coin: str) -> tuple[str, str]:
    """The perp's two possible names: XUSDT, or 1000XUSDT for coins quoted per thousand."""
    return f"{coin}USDT", f"1000{coin}USDT"


def _months(spot: CoinPath) -> tuple[Month, Month]:
    day1 = spot.announced + timedelta(days=1)
    return Month(day1.year, day1.month), Month(spot.removal.year, spot.removal.month)


def ingest_perps(
    paths: Sequence[CoinPath],
    data_dir: str,
    http: httpx.Client,
    on_coin: Callable[[str, str], None] | None = None,
) -> dict[str, str | None]:
    """Each measured coin's perp daily bars and funding for the months its window spans.

    Returns the perp symbol found for each coin (None: Binance's archive has neither name
    for those months). Bars go to the binance-um venue; funding to data/funding/.
    """
    store = ParquetStore(data_dir, venue=PERP_VENUE)
    client = BinanceVisionClient(http, base=UM_KLINES_URL)
    found: dict[str, str | None] = {}
    for spot in paths:
        start, end = _months(spot)
        found[spot.coin] = None
        for symbol in perp_symbols(spot.coin):
            got = client.fetch_range(symbol, Interval.D1, start, end)
            if not got.frames:
                continue
            store.write(symbol, Interval.D1, pl.concat(got.frames))
            with suppress(funding.FundingError):  # no funding file: the study reports it
                funding.save(funding.fetch(symbol, start, end, http), data_dir)
            found[spot.coin] = symbol
            break
        if on_coin is not None:
            on_coin(spot.coin, found[spot.coin] or "no perp")
    return found


def load_perp(
    data_dir: str, spot: CoinPath, symbol: str
) -> tuple[pl.DataFrame, pl.DataFrame | None]:
    """The perp's stored bars around the window, and its funding (None if not stored)."""
    start, _ = _months(spot)
    first = datetime(start.year, start.month, 1, tzinfo=UTC)
    last = _midnight(spot.removal) + timedelta(days=1)
    bars = ParquetStore(data_dir, venue=PERP_VENUE).read(symbol, Interval.D1, first, last)
    try:
        rates: pl.DataFrame | None = funding.load(data_dir, symbol)
    except ValueError:
        rates = None
    return bars, rates
