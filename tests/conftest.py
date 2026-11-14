"""Shared fixtures: synthetic archives byte-compatible with data.binance.vision."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    # A fixed timestamp: writestr(name) stamps the current time, so two builds of the same
    # archive a second apart (the file and its checksum) could differ and fail at random.
    entry = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(entry, body, compress_type=zipfile.ZIP_DEFLATED)
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


REAL_RESEARCH = Path(__file__).resolve().parents[1] / "research" / "strategies"


def _research_snapshot() -> dict[str, float]:
    if not REAL_RESEARCH.exists():
        return {}
    return {str(p): p.stat().st_mtime for p in REAL_RESEARCH.rglob("*") if p.is_file()}


@pytest.fixture(autouse=True)
def _never_touch_real_research() -> Iterator[None]:
    """Fail any test that writes to the repo's research notes. The API defaults its
    research folder to research/ relative to where it runs, which is the repo during
    tests; a test that forgets research_dir would write fake notes among the real ones."""
    before = _research_snapshot()
    yield
    after = _research_snapshot()
    assert after == before, "this test changed research/strategies; pass research_dir=tmp_path"
