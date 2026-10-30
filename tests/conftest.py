"""Shared fixtures: synthetic archives byte-compatible with data.binance.vision."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

ArchiveFactory = Callable[..., bytes]


def make_rows(
    start: datetime, n: int, step: timedelta, *, microseconds: bool = False
) -> list[list[str]]:
    """Plausible bars in Binance's raw CSV column order."""
    scale = 1_000_000 if microseconds else 1_000
    rows = []
    price = 40_000.0
    for i in range(n):
        open_t = start + i * step
        o = price
        c = price * (1 + (0.001 if i % 2 == 0 else -0.0008))
        h, lo = max(o, c) * 1.0005, min(o, c) * 0.9995
        vol = 10.0 + i
        rows.append(
            [
                str(int(open_t.timestamp() * scale)),
                f"{o:.2f}",
                f"{h:.2f}",
                f"{lo:.2f}",
                f"{c:.2f}",
                f"{vol:.6f}",
                str(int((open_t + step).timestamp() * scale) - 1),
                f"{vol * c:.6f}",
                str(100 + i),
                f"{vol / 2:.6f}",
                f"{vol * c / 2:.6f}",
                "0",
            ]
        )
        price = c
    return rows


def to_zip(
    rows: list[list[str]], name: str = "BTCUSDT-1h-2024-01.csv", header: bool = False
) -> bytes:
    body = "\n".join(",".join(r) for r in rows) + "\n"
    if header:
        body = (
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
            + body
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, body)
    return buf.getvalue()


def checksum_for(payload: bytes, filename: str) -> str:
    return f"{hashlib.sha256(payload).hexdigest()}  {filename}\n"


@pytest.fixture
def make_archive() -> ArchiveFactory:
    def _make(
        start: datetime = datetime(2024, 1, 1, tzinfo=UTC),
        n: int = 24,
        step: timedelta = timedelta(hours=1),
        microseconds: bool = False,
        header: bool = False,
    ) -> bytes:
        return to_zip(make_rows(start, n, step, microseconds=microseconds), header=header)

    return _make
