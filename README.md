# Kestrel

A research-to-production platform for systematic crypto trading, built so that a
strategy behaves the same way in research, backtest, paper trading and live
trading.

> **Status:** Data infrastructure, API and dashboard are built. Backtester is next.
> No strategies yet, by design.

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
| 1. Data | Binance bulk ingestion, canonical schema, Parquet store, quality checks | Done |
| 1.5 Platform | FastAPI service, job runner, React dashboard | Done |
| 2. Backtester | Vectorised research engine, event-driven validation engine, cost model, metrics | Planned |
| 3. Validation | Walk-forward, parameter robustness, trial log, deflated Sharpe | Planned |
| 4. Execution | `ExecutionVenue` adapters (sim, Binance testnet, live), reconciliation, kill switch, alerting | Planned |
| 5. Perps | Funding, basis, margin model | Planned |

## Quick start

Requires Python 3.11+ and Node 18+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
(cd frontend && npm install && npm run build)   # builds the dashboard into the package

# Run the platform: dashboard at http://127.0.0.1:8000, API docs at /api/docs
kestrel serve
```

Everything the dashboard does is also available from the CLI:

```bash
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

## Dashboard

One place to run every operation. Long-running work (ingestion now, backtests and
validation runs later) executes as a **job** with live progress, logs and
cancellation, so nothing blocks and every run leaves a record.

| Page | What it does |
|---|---|
| Overview | Platform health, data coverage, active and recent jobs, roadmap |
| Data | Ingest from Binance, browse datasets, candlestick + volume chart, quality audit with gap list |
| Jobs | Every operation with status, progress, duration, logs and cancel |
| Backtests | *Phase 2:* equity and drawdown, cost attribution, trade blotter, run comparison |
| Trading | *Phase 4:* positions, live vs backtest drift, risk limits, kill switch |

## Architecture

```
            ┌──────────────────────────┐
            │  React dashboard (Vite)  │   frontend/
            └────────────┬─────────────┘
                         │ HTTP/JSON (OpenAPI at /api/docs)
            ┌────────────▼─────────────┐
            │  FastAPI + JobManager    │   kestrel.api   (thin adapter, no logic)
            └────────────┬─────────────┘
                         │ plain function calls
┌────────────────────────▼─────────────────────────────────────────────┐
│ Engine: kestrel.data (and later kestrel.backtest, kestrel.execution) │
│ never imports the API, so CLI, scripts and tests use it directly     │
└──────────────────────────────────────────────────────────────────────┘
```

The engine has no idea a web server exists. The API is a thin adapter over it and
the dashboard only talks to the API, so each layer can be tested and replaced on
its own.

### Data pipeline

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
| `kestrel.api.app` | REST endpoints for datasets, bars, quality and jobs |
| `kestrel.api.jobs` | Thread-pool job runner: progress, logs, cooperative cancellation |
| `kestrel.cli` | `ingest`, `check`, `list`, `serve` commands |
| `frontend/` | React + TypeScript dashboard, typed API client, TradingView Lightweight Charts |

### Notable details

- **Timestamp units.** Binance spot archives switched from milliseconds to
  microseconds on 2025-01-01. Units are detected per row, and a test asserts that
  the same bars encoded both ways parse to identical frames.
- **Atomic writes.** Each month is written to a temp file and renamed, so a crash
  cannot leave a half-written partition.
- **Offline tests.** HTTP is injected, so the suite runs against byte-compatible
  fixture archives with no network. A `network`-marked test checks the real site.

- **Jobs never fake success.** An ingest that finds nothing to download fails
  with a clear reason instead of reporting success with zero data.

## Development

```bash
kestrel serve --reload                  # API with auto-reload on :8000
cd frontend && npm run dev              # dashboard with hot reload on :5173 (proxies /api)

ruff check . && ruff format --check .   # lint + format
mypy                                    # strict type-checking
pytest -m "not network"                 # offline suite
pytest -m network                       # live check against data.binance.vision
cd frontend && npm run typecheck        # strict TypeScript
```

CI runs the Python checks on 3.11 to 3.13 and type-checks and builds the
dashboard on every push.

Charts use [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache 2.0).

## Disclaimer

Personal research project. Nothing here is financial advice.
