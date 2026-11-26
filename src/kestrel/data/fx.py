"""Daily forex candles from OANDA (P6): bid, ask and mid, for the pairs fixed in ``[fx]``.

Source: OANDA's v20 REST API on a practice account (``KESTREL_OANDA_TOKEN``, read from the
environment only, never logged or stored). Practice and live accounts serve the same
historical prices. Read-only: this module never calls an order endpoint.

**The day.** Forex trades 24 hours from Sunday 17:00 to Friday 17:00 New York time, and
its day closes at 17:00 New York. Each candle is aligned to that close, so ``open_time``
(the bar's identity, tz-aware UTC) is 22:00 UTC in northern winter and 21:00 UTC in
summer, and a normal week has five candles opening Sunday to Thursday.

**Prices.** Each candle has bid, ask and mid OHLC; ``open/high/low/close`` are mid.
A buy fills at the ask and a sell at the bid, so the spread is in the data rather than
assumed. ``ticks`` is OANDA's tick count, the only volume forex has.

**Quality** (``check``): reported, never repaired. Duplicates, misaligned stamps, OHLC
violations, crossed quotes (bid above ask) and non-positive prices are integrity errors,
and a pair with any is not saved. Gaps other than the weekend are listed (holidays are
expected). Days with a single tick are counted: before 2005 OANDA's history is a daily
snapshot (open equals close), not a traded range.

What the first ingest found (2026-11-23, all 10 pairs clean on integrity): 2002-2004 is
snapshots (1-2 ticks a day, no real range), so research starts in 2005. From 2005 to 2013
OANDA also printed thin weekend candles (60-100 a year opening Saturday, a median of a few
hundred ticks against tens of thousands on weekdays); from 2014 there are none. Luke's
decision (2026-11-23): strategies drop them (``trading_days``); a Saturday quote that thin
isn't a tradable day. The stored file keeps them, as OANDA served them.

Stored as ``<data>/fx/oanda/<PAIR>-1d.parquet``, one file per pair, replaced atomically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Literal

import httpx
import polars as pl

from kestrel.atomic import write_atomically
from kestrel.data.schema import UTC_US

PRACTICE = "https://api-fxpractice.oanda.com"
TOKEN_ENV = "KESTREL_OANDA_TOKEN"
NEW_YORK = "America/New_York"
PAGE = 5000
DayEnd = Literal["ny", "utc"]
"""Where a daily candle closes: 17:00 New York (the market's own day, the default) or
00:00 UTC. A forex result must hold for both (TODO, 2026-11-26: a debunked forex rule lost
money with midnight days); UTC candles are stored apart and never replace the New York
ones. A UTC trading week is Monday to Friday (Saturday and Sunday opens are thin)."""
"""OANDA's largest candle count per request."""
SIDES = ("bid", "ask")
PRICES = ("open", "high", "low", "close")

FX_SCHEMA: dict[str, pl.DataType] = {
    "open_time": UTC_US,
    **{p: pl.Float64() for p in PRICES},
    **{f"{side}_{p}": pl.Float64() for side in SIDES for p in PRICES},
    "ticks": pl.Int64(),
}


class FxError(ValueError):
    """A reply that can't be read, or a missing token: reported, never repaired."""


def instrument(pair: str) -> str:
    """OANDA's name for a pair: EURUSD -> EUR_USD."""
    pair = pair.upper()
    if len(pair) != 6 or not pair.isalpha():
        raise FxError(f"a pair is six letters like EURUSD, got {pair!r}")
    return f"{pair[:3]}_{pair[3:]}"


def pip(pair: str) -> float:
    """The quote's pip: 0.01 for yen pairs, else 0.0001."""
    return 0.01 if pair.upper().endswith("JPY") else 0.0001


def token() -> str:
    value = os.environ.get(TOKEN_ENV, "").strip()
    if not value:
        raise FxError(f"set {TOKEN_ENV} (an OANDA practice account's API token) first")
    return value


def _stamp(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(candles: list[dict[str, object]]) -> pl.DataFrame:
    """OANDA candles to rows (complete candles only; the forming one is left out)."""
    rows: dict[str, list[object]] = {k: [] for k in FX_SCHEMA}
    for c in candles:
        if not c.get("complete"):
            continue
        try:
            when = datetime.fromisoformat(str(c["time"])[:19]).replace(tzinfo=UTC)
            sides = {"": c["mid"], "bid_": c["bid"], "ask_": c["ask"]}
            values = {
                f"{prefix}{p}": float(side[p[0]])  # type: ignore[index]
                for prefix, side in sides.items()
                for p in PRICES
            }
            ticks = int(c["volume"])  # type: ignore[call-overload]
        except (KeyError, TypeError, ValueError) as e:
            raise FxError(f"unreadable candle {str(c)[:120]}: {e}") from None
        rows["open_time"].append(when)
        for k, v in values.items():
            rows[k].append(v)
        rows["ticks"].append(ticks)
    return pl.DataFrame(rows, schema=FX_SCHEMA)


def fetch_daily(
    pair: str,
    http: httpx.Client,
    auth: str,
    start: datetime,
    base: str = PRACTICE,
    day_end: DayEnd = "ny",
) -> pl.DataFrame:
    """Every complete daily candle from ``start``, paged ``PAGE`` at a time."""
    frames: list[pl.DataFrame] = []
    since, first = start, True
    while True:
        r = http.get(
            f"{base}/v3/instruments/{instrument(pair)}/candles",
            params={
                "price": "MBA",
                "granularity": "D",
                "from": _stamp(since),
                "count": PAGE,
                "includeFirst": "true" if first else "false",
                "dailyAlignment": 17 if day_end == "ny" else 0,
                "alignmentTimezone": NEW_YORK if day_end == "ny" else "UTC",
            },
            headers={"Authorization": f"Bearer {auth}"},
        )
        if r.status_code != 200:
            # The body names the problem; the request (and its token) is never echoed.
            raise FxError(f"OANDA {pair}: HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        candles = body.get("candles") if isinstance(body, dict) else None
        if not isinstance(candles, list):
            raise FxError(f"OANDA {pair}: unexpected reply {str(body)[:120]}")
        frames.append(parse(candles))
        if len(candles) < PAGE:
            break
        since = datetime.fromisoformat(str(candles[-1]["time"])[:19]).replace(tzinfo=UTC)
        first = False
    return pl.concat(frames) if frames else pl.DataFrame(schema=FX_SCHEMA)


# ---------- quality ----------


@dataclass(frozen=True)
class FxGap:
    after: datetime
    before: datetime
    missing_days: int
    """Weekdays (New York) with no candle between the two."""


@dataclass
class FxQuality:
    pair: str
    rows: int
    first: datetime | None
    last: datetime | None
    duplicates: int = 0
    unsorted: bool = False
    misaligned: int = 0
    ohlc_violations: int = 0
    crossed: int = 0
    non_positive: int = 0
    nulls: int = 0
    weekend_candles: int = 0
    """Candles opening on a Friday or Saturday (New York): outside the trading week."""
    single_tick_days: int = 0
    last_single_tick: datetime | None = None
    gaps: list[FxGap] = field(default_factory=list)
    median_spread_pips: float | None = None
    """Median closing spread before the forex holdout (a cost, never a result)."""

    @property
    def is_clean(self) -> bool:
        """No integrity errors. Gaps and single-tick days are reported, not fatal."""
        return not (
            self.duplicates
            or self.unsorted
            or self.misaligned
            or self.ohlc_violations
            or self.crossed
            or self.non_positive
            or self.nulls
        )

    def summary(self) -> str:
        spread = "n/a" if self.median_spread_pips is None else f"{self.median_spread_pips:.2f}"
        last_single = (
            "none" if self.last_single_tick is None else f"{self.last_single_tick:%Y-%m-%d}"
        )
        lines = [
            f"{self.pair}: {self.rows:,} days, {self.first:%Y-%m-%d} to {self.last:%Y-%m-%d}"
            if self.first and self.last
            else f"{self.pair}: no data",
            f"  duplicates {self.duplicates} · misaligned {self.misaligned} · OHLC violations "
            f"{self.ohlc_violations} · crossed quotes {self.crossed} · non-positive "
            f"{self.non_positive} · nulls {self.nulls}",
            f"  weekend candles {self.weekend_candles} · single-tick days "
            f"{self.single_tick_days} (last {last_single}) · median spread "
            f"{spread} pips before the holdout",
            f"  gaps besides weekends: {len(self.gaps)} "
            f"({sum(g.missing_days for g in self.gaps)} weekdays)",
        ]
        for g in self.gaps[:5]:
            lines.append(f"    {g.after:%Y-%m-%d} -> {g.before:%Y-%m-%d} ({g.missing_days} days)")
        if len(self.gaps) > 5:
            lines.append(f"    ... and {len(self.gaps) - 5} more")
        lines.append(f"  verdict: {'CLEAN' if self.is_clean else 'FAILED (not saved)'}")
        return "\n".join(lines)


def _weekdays_between(after: datetime, before: datetime, day_end: DayEnd = "ny") -> int:
    """Trading days strictly between two opens: candles opening Sunday to Thursday in New
    York, or Monday to Friday in UTC."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(NEW_YORK if day_end == "ny" else "UTC")
    opens = (6, 0, 1, 2, 3) if day_end == "ny" else (0, 1, 2, 3, 4)
    d = after.astimezone(zone).date() + timedelta(days=1)
    end = before.astimezone(zone).date()
    n = 0
    while d < end:
        if d.weekday() in opens:
            n += 1
        d += timedelta(days=1)
    return n


def _weekend(day_end: DayEnd) -> list[int]:
    """polars weekdays (Monday 1) of candles outside the trading week."""
    return [5, 6] if day_end == "ny" else [6, 7]


def _local(day_end: DayEnd) -> pl.Expr:
    return pl.col("open_time").dt.convert_time_zone(NEW_YORK if day_end == "ny" else "UTC")


def check(
    frame: pl.DataFrame, pair: str, holdout_start: datetime, day_end: DayEnd = "ny"
) -> FxQuality:
    if frame.is_empty():
        return FxQuality(pair, 0, None, None)
    t = pl.col("open_time")
    report = FxQuality(
        pair,
        rows=frame.height,
        first=frame["open_time"].min(),  # type: ignore[arg-type]
        last=frame["open_time"].max(),  # type: ignore[arg-type]
        duplicates=frame.height - frame["open_time"].n_unique(),
        unsorted=not frame["open_time"].is_sorted(),
        nulls=int(frame.null_count().sum_horizontal().item()),
    )
    local = _local(day_end)
    hour = 17 if day_end == "ny" else 0
    report.misaligned = frame.filter(
        (local.dt.hour() != hour) | (local.dt.minute() != 0) | (local.dt.second() != 0)
    ).height
    report.weekend_candles = frame.filter(local.dt.weekday().is_in(_weekend(day_end))).height
    violations = 0
    for prefix in ("", "bid_", "ask_"):
        cols = [f"{prefix}{p}" for p in PRICES]
        violations += frame.filter(
            (pl.col(f"{prefix}high") < pl.max_horizontal(cols))
            | (pl.col(f"{prefix}low") > pl.min_horizontal(cols))
        ).height
    report.ohlc_violations = violations
    report.crossed = frame.filter(
        pl.any_horizontal(pl.col(f"bid_{p}") > pl.col(f"ask_{p}") for p in PRICES)
    ).height
    price_cols = [c for c in FX_SCHEMA if c not in ("open_time", "ticks")]
    report.non_positive = frame.filter(pl.any_horizontal(pl.col(c) <= 0 for c in price_cols)).height
    single = frame.filter(pl.col("ticks") <= 1)
    report.single_tick_days = single.height
    report.last_single_tick = single["open_time"].max() if single.height else None  # type: ignore[assignment]

    ordered = frame.select("open_time").unique().sort("open_time")["open_time"].to_list()
    for a, b in pairwise(ordered):
        missing = _weekdays_between(a, b, day_end)
        if missing:
            report.gaps.append(FxGap(a, b, missing))

    before = frame.filter(t < holdout_start)
    if before.height:
        spread = ((pl.col("ask_close") - pl.col("bid_close")) / pip(pair)).median()
        report.median_spread_pips = float(before.select(spread).item())
    return report


def trading_days(frame: pl.DataFrame, day_end: DayEnd = "ny") -> tuple[pl.DataFrame, int]:
    """The candles of the trading week (opening Sunday to Thursday, New York; Monday to
    Friday on UTC days), and how many weekend candles were dropped. Every strategy and
    backtest reads forex this way."""
    kept = frame.filter(~_local(day_end).dt.weekday().is_in(_weekend(day_end)))
    return kept, frame.height - kept.height


# ---------- storage ----------


def path(data_dir: Path | str, pair: str, day_end: DayEnd = "ny") -> Path:
    suffix = "" if day_end == "ny" else "-utc"
    return Path(data_dir) / "fx" / "oanda" / f"{pair.upper()}-1d{suffix}.parquet"


def save(frame: pl.DataFrame, data_dir: Path | str, pair: str, day_end: DayEnd = "ny") -> Path:
    target = path(data_dir, pair, day_end)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(
        target, lambda tmp: frame.select(list(FX_SCHEMA)).sort("open_time").write_parquet(tmp)
    )
    return target


def load(data_dir: Path | str, pair: str, day_end: DayEnd = "ny") -> pl.DataFrame:
    target = path(data_dir, pair, day_end)
    if not target.exists():
        flag = "" if day_end == "ny" else " --day-end utc"
        raise FxError(f"no {pair.upper()} data ({day_end} days); run kestrel ingest-fx{flag}")
    return pl.read_parquet(target)
