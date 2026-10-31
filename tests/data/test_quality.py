from __future__ import annotations

from datetime import timedelta

import polars as pl
import pytest

from kestrel.data.binance_vision import parse_archive
from kestrel.data.quality import check_klines
from kestrel.data.schema import Interval
from tests.conftest import ArchiveFactory


@pytest.fixture
def clean(make_archive: ArchiveFactory) -> pl.DataFrame:
    return parse_archive(make_archive(n=24))


def test_clean_series_passes(clean: pl.DataFrame) -> None:
    report = check_klines(clean, Interval.H1)
    assert report.is_clean
    assert report.gaps == []
    assert report.rows == 24


def test_detects_gap_and_counts_missing_bars(clean: pl.DataFrame) -> None:
    holed = clean.filter(~pl.int_range(pl.len()).is_in([5, 6, 7]))
    report = check_klines(holed, Interval.H1)
    assert report.is_clean  # gaps are reported, not fatal
    assert len(report.gaps) == 1
    assert report.missing_bars == 3


def test_detects_duplicates(clean: pl.DataFrame) -> None:
    report = check_klines(pl.concat([clean, clean.head(2)]), Interval.H1)
    assert report.duplicates == 2
    assert report.unsorted
    assert not report.is_clean


def test_detects_ohlc_violation(clean: pl.DataFrame) -> None:
    bad = clean.with_columns(
        pl.when(pl.int_range(pl.len()) == 3)
        .then(pl.col("low") * 0.5)
        .otherwise(pl.col("high"))
        .alias("high")
    )
    report = check_klines(bad, Interval.H1)
    assert report.ohlc_violations == 1
    assert not report.is_clean


def test_detects_misaligned_timestamp(clean: pl.DataFrame) -> None:
    bad = clean.with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(pl.col("open_time") + timedelta(minutes=7))
        .otherwise(pl.col("open_time"))
        .alias("open_time")
    )
    assert check_klines(bad, Interval.H1).misaligned == 1


def test_detects_non_positive_price_and_negative_volume(clean: pl.DataFrame) -> None:
    bad = clean.with_columns(
        pl.when(pl.int_range(pl.len()) == 1).then(0.0).otherwise(pl.col("open")).alias("open"),
        pl.when(pl.int_range(pl.len()) == 2).then(-1.0).otherwise(pl.col("volume")).alias("volume"),
    )
    report = check_klines(bad, Interval.H1)
    assert report.non_positive_prices == 1
    assert report.negative_volumes == 1


def test_detects_nulls(clean: pl.DataFrame) -> None:
    bad = clean.with_columns(
        pl.when(pl.int_range(pl.len()) == 4).then(None).otherwise(pl.col("close")).alias("close")
    )
    assert check_klines(bad, Interval.H1).nulls == 1


def test_empty_frame() -> None:
    report = check_klines(pl.DataFrame(), Interval.H1)
    assert report.rows == 0
    assert report.is_clean


def test_summary_renders(clean: pl.DataFrame) -> None:
    assert "CLEAN" in check_klines(clean, Interval.H1).summary()


def test_summary_truncates_long_gap_list(clean: pl.DataFrame) -> None:
    every_other = clean.filter(pl.int_range(pl.len()) % 2 == 0)  # 11 single-bar gaps
    report = check_klines(every_other, Interval.H1)
    assert len(report.gaps) == 11
    summary = report.summary()
    assert "... and 1 more" in summary
    assert "FAILED" not in summary  # gaps alone never fail the audit
