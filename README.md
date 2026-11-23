# Kestrel

A research-to-production platform for systematic crypto trading, built so that a
strategy behaves the same way in research, backtest, paper trading and live
trading, and so that a result can't look better than it is.

> **Status (2026-11-22):** Data, backtester, validation, paper trading and the dashboard
> are built and tested. 88 pre-registered research notes and 866 counted trials later,
> three strategies are being paper-traded on Binance's Spot Testnet: trend following with
> volatility targeting (BTC, ETH, SOL), a BTC-regime trend rule found by an
> explore-then-confirm experiment (ETH, SOL), and a BTC-regime rotation among large coins.
> None is a proven edge: all fail the luck test once every trial is counted, so the
> forward record decides. **Start with the one-page
> [research summary](docs/research-summary.md)**; every number is in
> [research/findings.md](research/findings.md). No strategy trades real money yet, by
> design.

## Why this exists

Most retail trading systems fail in the gap between backtest and live: lookahead
bias, unrealistic fills, ignored costs, and research code that is rewritten
(differently) for production. Kestrel is built around closing that gap.

**Design principles**

1. **One strategy code path.** Strategies talk to an `ExecutionVenue` interface,
   never to an exchange. Backtest, paper and live share one rebalance step, and a
   day-by-day paper replay must reproduce the backtest exactly.
2. **Data you can trust.** Every source is normalised into one canonical schema,
   checksum-verified, and audited for gaps, duplicates and OHLC integrity before
   use. Problems are reported, never silently patched.
3. **Costs are first-class.** Fees, spread and market impact are modelled on every fill.
4. **Honest research.** Hypotheses are frozen before testing, every setting tried is
   counted, results are deflated for the number of trials, and a holdout year was
   reserved before any research and is used once.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1. Data | Binance bulk ingestion, canonical schema, Parquet store, quality checks, live updates | Done |
| 1.5 Platform | FastAPI service, job runner, React dashboard | Done |
| 2. Backtester | Event-driven and vectorised engines (parity-tested), cost model, metrics with uncertainty, stops/targets with risk sizing, resting limit orders, a portfolio engine for coin baskets | Done |
| 3. Validation | Deflated Sharpe over every trial, walk-forward, robustness, random-entry control, the holdout test, one scorecard graded on every run | Done |
| 3.5 Research method | Pre-registered notes, decisions per strategy and market, an explore-then-confirm experiment on a fixed split of coins | Done |
| 4. Execution | Paper trading through the backtest's own code (single coins and coin baskets) on a simulated venue or Binance Spot Testnet, reconciliation, risk limits, kill switch, tracking against the model, a decay check, alerts, scheduled runs | Running (live venue later) |
| Parked | Perps (not offered to Australian retail; ASIC caps retail crypto leverage at 2:1); multi-strategy portfolios; a settings page | Parked |

## Quick start

Requires Python 3.11+ and Node 18+.
Python packages are pinned with hashes in `requirements-dev.lock` (CI installs the same);
to upgrade, `pip-compile --extra dev --generate-hashes --strip-extras --output-file
requirements-dev.lock pyproject.toml`, then run `scripts/check.sh`.

```bash
python -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements-dev.lock && pip install --no-deps -e .
(cd frontend && npm install && npm run build)   # builds the dashboard into the package

# Dashboard at http://127.0.0.1:8000, API docs at /api/docs
kestrel serve
```

Everything the dashboard does is also available from the CLI:

```bash
kestrel ingest BTCUSDT --interval 1d --start 2020-01      # download, verify, store
kestrel check BTCUSDT --interval 1d                       # audit it
kestrel backtest BTCUSDT --interval 1d --start 2020-01 --end 2025-10   # the benchmark
kestrel paper status tvt-btc                              # a paper account
```

Data lands in `./data` (override with `--data-dir` or `KESTREL_DATA`) as
Hive-partitioned Parquet, queryable directly from polars or DuckDB:

```python
import duckdb

duckdb.sql("""
    SELECT date_trunc('month', open_time) AS month, last(close ORDER BY open_time) AS close
    FROM 'data/klines/venue=binance/symbol=BTCUSDT/interval=1d/*.parquet'
    GROUP BY 1 ORDER BY 1
""")
```

## Dashboard

![Kestrel dashboard: a backtest and its scorecard](docs/dashboard-light.png)

<sub>A real run from the research: trend following on BTC, 2020-25, after costs. It beats BTC
held at the same exposure (Sharpe 1.21 vs 1.07) but fails the luck and walk-forward tests,
so it is paper-traded as risk-managed holding, not called an edge. Light and dark themes
(the [Thesis page in dark](docs/dashboard-dark.png)); toggle in the top bar.</sub>

The dashboard reads; research and trading actions run from the CLI or as **jobs** with
live progress, logs and cancellation.

| Page | What it shows |
|---|---|
| Tour (top right) | A 12-stop guided tour of how a result is judged, from the question to a strategy failing its tests honestly to the limits, ending back on the Overview |
| Thesis (top right) | The one-page research summary ([docs/research-summary.md](docs/research-summary.md)) |
| Overview | What to pursue (one entry per strategy, with the coins it trades), holdout tests, the research pipeline, recent studies |
| Trading | Each paper account: equity, positions, what it's waiting for, decisions, fills, safety state, tracking against the model, the decay check against its own backtest |
| Research | Every decision grouped by strategy family, the luck bar over time, the exploration experiment coin by coin, the findings |
| Strategies | Research notes, frozen before testing, with versions, the code they run, signals, their trial log and a research assistant that never sees results |
| Backtests | Saved runs with the scorecard, equity and drawdown, costs, start-date and bootstrap analysis, fills and provenance; the shorting, event and signal studies |
| Charts | Any market and bar size, a fixed menu of textbook indicators, each with a plain-English reading |
| More | What a trade costs (the live cost model and the minute-chart cost gate), Rules, Regimes (HMM states), Data |

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
┌────────────────────────▼──────────────────────────────────────────────┐
│ Engine: data, backtest, validation, strategies, studies, execution    │
│ never imports the API, so the CLI, scripts and tests use it directly  │
└───────────────────────────────────────────────────────────────────────┘
```

The engine has no idea a web server exists. The API is a thin adapter over it and
the dashboard only talks to the API, so each layer can be tested and replaced on
its own.

| Package | Responsibility |
|---|---|
| `kestrel.data` | Canonical schema, Binance archive download and checksums, atomic Parquet store, quality audit, live updates, the coin universe (delisted coins included), features (funding, Fear & Greed, BTC's close, stablecoin supply, attention, news), delisting announcements, daily forex bid/ask from OANDA (`kestrel ingest-fx`, P6) |
| `kestrel.backtest` | Cost model, event-driven engine (weights or brackets, limit entries), vectorised engine, portfolio engine, the forex engine (signed and leveraged weights across pairs, bid/ask fills, financing, AUD), per-coin spread bands, metrics and analysis, saved runs |
| `kestrel.strategy`, `kestrel.strategies` | The strategy interfaces, the registry, the lookahead check, and every strategy and control tested |
| `kestrel.indicators` | One tested implementation of each indicator |
| `kestrel.studies` | Raw-effect studies run before any trading rule (anchor day, volatile spells, weekly signals, delistings, cross-sectional funding and flow) and the minute-chart cost gate |
| `kestrel.validation` | Deflated Sharpe and the trial pools, walk-forward, robustness, the random-entry control, the holdout test, the scorecard, the confirmation run |
| `kestrel.research`, `kestrel.decisions` | Versioned, frozen research notes; the one rule set for starting a backtest; decisions per strategy and market |
| `kestrel.exploration` | The explore-then-confirm split and its engine lock |
| `kestrel.regimes` | A Gaussian HMM on filtered probabilities, refitted monthly, on daily bars (the preset) or 4h bars (`FAST_PRESET`); describes, never trades |
| `kestrel.execution` | The venue interface, the shared rebalance step, the paper runner and ledger, the Binance testnet venue, safety limits and kill switch, tracking against the model |
| `kestrel.assistant` | The research assistant on Claude, given the note and code only, never results |
| `kestrel.api` | REST endpoints and the job runner |
| `kestrel.cli` | Every operation, from `ingest` to `paper run` |
| `frontend/` | React + TypeScript dashboard, typed API client, theme tokens, TradingView Lightweight Charts |

### Notable details

- **Lookahead is checked, not trusted.** Before a backtest runs, the engine recomputes
  the strategy on truncated histories; a strategy that peeks at a future bar gives
  different answers and is refused.
- **Two engines that must agree.** A vectorised engine re-implements the event engine's
  model, including weight drift between trades; a parity test requires daily returns to
  match within 0.01%.
- **Costs, attributed.** Every fill splits its cost into fee, spread and square-root market
  impact. A BTC round trip costs about 17 bps (7.5 bps fees with the BNB discount). Smaller
  coins pay a spread band set by their daily volume. Impact is sized from the last closed
  bar's volume, so a fill never uses its own bar.
- **Stops, targets and limits, measured in R.** Bracket strategies are sized from the risk
  per trade and the stop distance. A bar reaching both stop and target counts as the stop.
  A resting limit order fills only when a bar reaches it, and on its fill bar only the
  stop can end the trade.
- **Every run is a counted trial.** The deflated Sharpe measures each result against the
  best of every configuration tried (805 so far, a luck bar of 2.29). The dashboard shows
  how the bar has moved.
- **A pre-registered holdout.** Data from 2025-11-01 was reserved on 2026-10-31. The
  engine refuses it unless a finished strategy's one holdout test asks.
- **Explore, then confirm.** Coins were split once, by a seeded draw balanced by volume.
  Ideas are searched freely on one half without being counted. A few graduates are run
  once on the locked other half, under a test fixed in advance.
- **Paper trading is the backtest.** The paper runner handles each closed bar as the engine
  does (mark, decide, trade at the next open) and keeps an append-only ledger. It refreshes
  every market a strategy reads, and refuses to decide on missing or stale data. Breaches
  halt an account until it is resumed by hand, with a reason.
- **An AI assistant outside the tuning loop.** The research assistant (Claude) sees the
  note and code, never results, so it can challenge an idea but can't fit it.
- **Config is versioned, not a settings page.** `kestrel.toml` has no defaults in code and
  rejects unknown keys. Every run stores the config, its fingerprint and the git commit.

## Development

```bash
kestrel serve --reload                  # API with auto-reload on :8000
cd frontend && npm run dev              # dashboard with hot reload on :5173 (proxies /api)

scripts/check.sh                        # everything CI runs; stops at the first failure
```

`scripts/check.sh` runs ruff, the format check, strict mypy, the offline test suite (967
tests), a check that every decision's notes are committed, and the dashboard's tests (160)
and build. CI runs the same checks on every push, the Python ones on 3.11 to 3.13, all but
the commit check, which reads uncommitted files and so only makes sense locally.
`pytest -m network` checks the real data sources.

Charts use [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache 2.0).

## Disclaimer

Personal research project. Nothing here is financial advice.
