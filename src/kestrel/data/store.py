"""Local Parquet store for canonical klines.

Layout (Hive-style, readable by polars, DuckDB, pandas and Spark):

    <root>/klines/venue=binance/symbol=BTCUSDT/interval=1h/month=2024-01.parquet

One file per month makes writes idempotent: re-ingesting a month replaces
exactly that file, and a crash mid-write cannot corrupt neighbouring months
because each file is written to a temp path and atomically renamed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl

from kestrel.atomic import write_atomically
from kestrel.data.schema import KLINE_SCHEMA, Interval, conform


class ParquetStore:
    def __init__(self, root: Path | str, venue: str = "binance") -> None:
        self.root = Path(root)
        self.venue = venue

    def _dir(self, symbol: str, interval: Interval) -> Path:
        return (
            self.root
            / "klines"
            / f"venue={self.venue}"
            / f"symbol={symbol.upper()}"
            / f"interval={interval.value}"
        )

    def write(self, symbol: str, interval: Interval, df: pl.DataFrame) -> list[Path]:
        """Write bars, partitioned by month. Overwrites any month present in ``df``."""
        if df.is_empty():
            return []
        df = conform(df).sort("open_time")
        target_dir = self._dir(symbol, interval)
        target_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        keyed = df.with_columns(pl.col("open_time").dt.strftime("%Y-%m").alias("_month"))
        for (month,), part in keyed.group_by("_month", maintain_order=True):
            path = target_dir / f"month={month}.parquet"
            data = part.drop("_month")

            def save(tmp: Path, data: pl.DataFrame = data) -> None:
                data.write_parquet(tmp, compression="zstd", statistics=True)

            write_atomically(path, save)
            written.append(path)
        return written

    def scan(
        self,
        symbol: str,
        interval: Interval,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pl.LazyFrame:
        """Lazily scan stored bars with ``start <= open_time < end``."""
        files = sorted(self._dir(symbol, interval).glob("month=*.parquet"))
        if not files:
            return pl.LazyFrame(schema=KLINE_SCHEMA)
        lf = pl.scan_parquet(files)
        if start is not None:
            lf = lf.filter(pl.col("open_time") >= start)
        if end is not None:
            lf = lf.filter(pl.col("open_time") < end)
        return lf.sort("open_time")

    def read(
        self,
        symbol: str,
        interval: Interval,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pl.DataFrame:
        return self.scan(symbol, interval, start, end).collect()

    def datasets(self) -> list[tuple[str, Interval]]:
        """Every (symbol, interval) pair with at least one stored month."""
        base = self.root / "klines" / f"venue={self.venue}"
        found: list[tuple[str, Interval]] = []
        for sym_dir in sorted(base.glob("symbol=*")):
            for int_dir in sorted(sym_dir.glob("interval=*")):
                raw = int_dir.name.removeprefix("interval=")
                if raw in Interval._value2member_map_ and any(int_dir.glob("month=*.parquet")):
                    found.append((sym_dir.name.removeprefix("symbol="), Interval(raw)))
        return found

    def months(self, symbol: str, interval: Interval) -> list[str]:
        return sorted(
            p.stem.removeprefix("month=")
            for p in self._dir(symbol, interval).glob("month=*.parquet")
        )
