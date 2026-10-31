"""Data-quality checks for kline series.

Bad data silently produces fake edges, so every series is checked before it is
trusted. Each check reports what it found rather than "fixing" the data:
deciding how to treat a gap (forward-fill, drop, flag) is a research decision
that belongs in the open, not buried in the loader.

Note: Binance has genuine historical outages (e.g. exchange maintenance), so a
small number of gaps in raw data is expected and real. The report lets you see
them; it does not invent bars to cover them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import polars as pl

from kestrel.data.schema import Interval


@dataclass(frozen=True)
class Gap:
    after: datetime
    before: datetime
    missing_bars: int


@dataclass
class QualityReport:
    rows: int
    first: datetime | None
    last: datetime | None
    duplicates: int = 0
    unsorted: bool = False
    misaligned: int = 0
    ohlc_violations: int = 0
    non_positive_prices: int = 0
    negative_volumes: int = 0
    nulls: int = 0
    gaps: list[Gap] = field(default_factory=list)

    @property
    def missing_bars(self) -> int:
        return sum(g.missing_bars for g in self.gaps)

    @property
    def is_clean(self) -> bool:
        """True when there are no integrity errors. Gaps are reported, not fatal."""
        return not (
            self.duplicates
            or self.unsorted
            or self.misaligned
            or self.ohlc_violations
            or self.non_positive_prices
            or self.negative_volumes
            or self.nulls
        )

    def summary(self) -> str:
        lines = [
            f"rows:               {self.rows:,}",
            f"range:              {self.first} -> {self.last}",
            f"duplicates:         {self.duplicates}",
            f"unsorted:           {self.unsorted}",
            f"misaligned stamps:  {self.misaligned}",
            f"OHLC violations:    {self.ohlc_violations}",
            f"non-positive price: {self.non_positive_prices}",
            f"negative volume:    {self.negative_volumes}",
            f"nulls:              {self.nulls}",
            f"gaps:               {len(self.gaps)} ({self.missing_bars:,} missing bars)",
        ]
        for gap in self.gaps[:10]:
            lines.append(f"  - {gap.after} -> {gap.before} ({gap.missing_bars} bars)")
        if len(self.gaps) > 10:
            lines.append(f"  ... and {len(self.gaps) - 10} more")
        lines.append(f"verdict:            {'CLEAN' if self.is_clean else 'FAILED'}")
        return "\n".join(lines)


def check_klines(df: pl.DataFrame, interval: Interval) -> QualityReport:
    if df.is_empty():
        return QualityReport(rows=0, first=None, last=None)

    step = interval.duration
    step_us = int(step / timedelta(microseconds=1))
    t = pl.col("open_time")
    prices = ["open", "high", "low", "close"]

    report = QualityReport(
        rows=df.height,
        first=df["open_time"].min(),  # type: ignore[arg-type]
        last=df["open_time"].max(),  # type: ignore[arg-type]
        duplicates=df.height - df["open_time"].n_unique(),
        unsorted=not df["open_time"].is_sorted(),
        nulls=int(df.null_count().sum_horizontal().item()),
    )

    report.misaligned = df.filter(t.dt.epoch("us") % step_us != 0).height
    report.ohlc_violations = df.filter(
        (pl.col("high") < pl.max_horizontal(prices)) | (pl.col("low") > pl.min_horizontal(prices))
    ).height
    report.non_positive_prices = df.filter(pl.any_horizontal(pl.col(p) <= 0 for p in prices)).height
    report.negative_volumes = df.filter(
        (pl.col("volume") < 0) | (pl.col("quote_volume") < 0)
    ).height

    ordered = df.select("open_time").unique().sort("open_time")
    jumps = ordered.with_columns(prev=t.shift(1)).filter(t - pl.col("prev") > step)
    report.gaps = [
        Gap(
            after=row["prev"],
            before=row["open_time"],
            missing_bars=(row["open_time"] - row["prev"]) // step - 1,
        )
        for row in jumps.iter_rows(named=True)
    ]
    return report
