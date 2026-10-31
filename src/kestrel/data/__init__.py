"""Market data: ingestion, canonical schema, storage and quality checks."""

from kestrel.data.quality import QualityReport, check_klines
from kestrel.data.schema import KLINE_SCHEMA, Interval
from kestrel.data.store import ParquetStore

__all__ = ["KLINE_SCHEMA", "Interval", "ParquetStore", "QualityReport", "check_klines"]
