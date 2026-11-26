"""Many coins' bars in one long frame: the input to cross-sectional strategies.

One row per (symbol, open_time), sorted by symbol then time, with a ``history`` number
that starts at 0 and increases at every gap in a coin's bars. Binance reuses symbols:
LUNAUSDT is the original LUNA until 2022-05-13, then after an 18-day gap a different coin.
Anything computed over time (returns, volatility, spreads, eligibility) must stay within
one history, never across a gap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import polars as pl

from kestrel.data.binance_vision import Month
from kestrel.data.schema import Interval
from kestrel.data.store import ParquetStore

PANEL_COLUMNS = ("symbol", "open_time", "open", "high", "low", "close", "quote_volume")


def with_histories(panel: pl.DataFrame, interval: Interval) -> pl.DataFrame:
    """Sort by symbol and time and number each coin's unbroken histories."""
    gap = pl.col("open_time").diff().over("symbol") > interval.duration
    return panel.sort("symbol", "open_time").with_columns(
        history=gap.fill_null(False).cum_sum().over("symbol").cast(pl.Int32)
    )


OUTAGE = timedelta(days=1)
"""Less missing than this is an exchange outage, not a break in a coin's history: Binance's
2020-02-19 maintenance cost BTC and ETH two 4h bars. A day or more missing is a delisting, a
token swap or a reused symbol (findings 31), which a one-coin run refuses to cross."""


def gaps(bars: pl.DataFrame, interval: Interval) -> list[tuple[datetime, datetime]]:
    """Each break in one coin's sorted bars: (last bar before it, first bar after it), with a
    day or more missing between them (``OUTAGE``)."""
    times: list[datetime] = bars["open_time"].to_list()
    return [(a, b) for a, b in pairwise(times) if b - a - interval.duration >= OUTAGE]


def unbroken(bars: pl.DataFrame, interval: Interval) -> list[tuple[datetime, datetime]]:
    """The first and last bar of each unbroken history in one coin's sorted bars."""
    if bars.is_empty():
        return []
    times: list[datetime] = bars["open_time"].to_list()
    edges = gaps(bars, interval)
    starts = [times[0], *(after for _, after in edges)]
    ends = [*(before for before, _ in edges), times[-1]]
    return list(zip(starts, ends, strict=True))


def refuse_gaps(bars: pl.DataFrame, interval: Interval, symbol: str) -> None:
    """A one-coin run uses one unbroken history: nothing is computed across a gap, and a gap
    is reported, never bridged (findings 31). Raises ValueError naming the histories."""
    if not gaps(bars, interval):
        return
    spans = "; ".join(f"{a:%Y-%m-%d} to {b:%Y-%m-%d}" for a, b in unbroken(bars, interval))
    raise ValueError(
        f"{symbol} has a gap in its bars (a day or more missing: a delisting, token swap or "
        f"reused symbol), so these are separate histories: {spans}. Run on a range inside "
        "one of them; nothing is computed across a gap."
    )


def load_panel(
    store: ParquetStore, symbols: list[str], interval: Interval, start: Month, end: Month
) -> pl.DataFrame:
    """Every stored bar of ``symbols`` in ``start..end`` (inclusive months)."""
    after = end.next()
    lo = datetime(start.year, start.month, 1, tzinfo=UTC)
    hi = datetime(after.year, after.month, 1, tzinfo=UTC)
    frames = []
    for symbol in symbols:
        bars = store.read(symbol, interval, lo, hi)
        if bars.height:
            frames.append(bars.with_columns(symbol=pl.lit(symbol)).select(PANEL_COLUMNS))
    if not frames:
        return pl.DataFrame(
            schema={
                "symbol": pl.Utf8,
                "open_time": pl.Datetime("us", "UTC"),
                **{c: pl.Float64 for c in PANEL_COLUMNS[2:]},
                "history": pl.Int32,
            }
        )
    return with_histories(pl.concat(frames), interval)
