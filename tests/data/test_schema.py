from __future__ import annotations

from datetime import timedelta

import polars as pl
import pytest

from kestrel.data.schema import KLINE_SCHEMA, Interval, conform


@pytest.mark.parametrize(
    ("interval", "expected"),
    [
        (Interval.M1, timedelta(minutes=1)),
        (Interval.M15, timedelta(minutes=15)),
        (Interval.H4, timedelta(hours=4)),
        (Interval.D1, timedelta(days=1)),
    ],
)
def test_interval_duration(interval: Interval, expected: timedelta) -> None:
    assert interval.duration == expected


def test_conform_rejects_missing_columns() -> None:
    df = pl.DataFrame(schema={k: v for k, v in KLINE_SCHEMA.items() if k != "close"})
    with pytest.raises(ValueError, match="close"):
        conform(df)


def test_conform_orders_casts_and_drops_extra_columns() -> None:
    shuffled = {name: KLINE_SCHEMA[name] for name in reversed(KLINE_SCHEMA)}
    df = pl.DataFrame(schema={**shuffled, "trade_count": pl.Int32(), "ignore": pl.Utf8()})
    assert conform(df).schema == pl.Schema(KLINE_SCHEMA)
