# Kestrel

A research-to-production platform for systematic crypto trading, built so that a
strategy behaves the same way in research, backtest, paper trading and live
trading.

> **Status:** Phase 1 (data infrastructure) in progress. No strategies yet, by design.

## Why this exists

Most retail trading systems fail in the gap between backtest and live: lookahead
bias, unrealistic fills, ignored costs, and research code that is rewritten
(differently) for production. Kestrel is built around closing that gap.

**Design principles**

1. **One strategy code path.** Strategies talk to an `ExecutionVenue` interface,
   never to an exchange. Backtest, paper and live are interchangeable
   implementations, and parity tests fail the build if they disagree.
2. **Data you can trust.** Every source is normalised into one canonical schema,
   checksum-verified, and audited for gaps, duplicates and OHLC integrity before
   use. Problems are reported, never silently patched.
3. **Costs are first-class.** Fees, spread and slippage are modelled from the
   start, not bolted on after a backtest looks good.
4. **Honest research.** Hypotheses are written down before testing, every variant
   tried is logged, and a holdout period is reserved and touched once.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1. Data | Binance bulk ingestion, canonical schema, Parquet store, quality checks | **In progress** |
| 2. Backtester | Vectorised research engine, event-driven validation engine, cost model, metrics | Planned |
| 3. Validation | Walk-forward, parameter robustness, trial log, deflated Sharpe | Planned |
| 4. Execution | `ExecutionVenue` adapters (sim, Binance testnet, live), reconciliation, kill switch, alerting | Planned |
| 5. Perps | Funding, basis, margin model | Planned |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Download, checksum-verify and store hourly BTC/USDT history
kestrel ingest BTCUSDT --interval 1h --start 2020-01

# Audit it
kestrel check BTCUSDT --interval 1h
```

Data lands in `./data` (override with `--data-dir` or `KESTREL_DATA`) as
Hive-partitioned Parquet, queryable directly from polars or DuckDB:

```python
import duckdb

duckdb.sql("""
    SELECT date_trunc('day', open_time) AS day, last(close ORDER BY open_time) AS close
    FROM 'data/klines/venue=binance/symbol=BTCUSDT/interval=1h/*.parquet'
    GROUP BY 1 ORDER BY 1
""")
```

## Architecture (Phase 1)

```
data.binance.vision ──► BinanceVisionClient ──► parse_archive ──► ParquetStore ──► check_klines
   (monthly zips)        (download + SHA-256)   (canonical schema)  (month-partitioned,  (gaps, dupes,
                                                 ms/µs normalised)    atomic writes)       OHLC integrity)
```

| Module | Responsibility |
|---|---|
| `kestrel.data.schema` | Canonical kline schema and intervals; the contract every source must meet |
| `kestrel.data.binance_vision` | Download, checksum verification, parsing of Binance bulk archives |
| `kestrel.data.store` | Idempotent, atomic, month-partitioned Parquet storage |
| `kestrel.data.quality` | Integrity audit that reports rather than repairs |
| `kestrel.cli` | `ingest`, `check`, `list` commands |

### Notable details

- **Timestamp units.** Binance spot archives switched from milliseconds to
  microseconds on 2025-01-01. Units are detected per row, and a test asserts that
  the same bars encoded both ways parse to identical frames.
- **Atomic writes.** Each month is written to a temp file and renamed, so a crash
  cannot leave a half-written partition.
- **Offline tests.** HTTP is injected, so the suite runs against byte-compatible
  fixture archives with no network. A `network`-marked test checks the real site.

## Development

```bash
ruff check . && ruff format --check .   # lint + format
mypy                                    # strict type-checking
pytest -m "not network"                 # offline suite
pytest -m network                       # live check against data.binance.vision
```

CI runs all of the above on Python 3.11 to 3.13 for every push.

## Disclaimer

Personal research project. Nothing here is financial advice.
