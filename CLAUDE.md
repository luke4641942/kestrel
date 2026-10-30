# Kestrel: notes for Claude

Systematic crypto trading platform, built to be shown to quant employers. Quality
and honesty of results matter more than speed. Read README.md for architecture.

## Goals and constraints
- Spot only for now (Binance). Perps come later, once the platform is proven.
- Starting capital ~A$500, so strategies must be low-turnover (4h/daily horizons).
  Fees dominate at this size; never present a result without costs included.
- A strategy must behave identically in backtest, paper and live. Strategies talk
  to an `ExecutionVenue` interface, never to an exchange directly.
- Platform before strategies. No strategy research until the backtester is validated.
- Luke works in ~45-minute weekday sessions and longer on days off. Size tasks so
  one session ends with something merged, tested and working.

## Layers (dependencies point downward only)
1. `frontend/` React + TypeScript dashboard. Talks only to the API.
2. `kestrel.api` FastAPI adapter + JobManager. No business logic here.
3. Engine: `kestrel.data` now; `kestrel.backtest`, `kestrel.execution` next.
   The engine must never import from `kestrel.api`.

Long-running operations (ingest, backtests, validation) run as jobs via
`JobManager`, reporting progress and logs and checking cancellation.

## Conventions
- Python 3.11+, polars (not pandas), strict mypy, ruff. Type everything.
- Timestamps: tz-aware UTC, microseconds. `open_time` is a bar's identity.
- Data problems are reported, never silently repaired.
- Every change ships with tests. HTTP is injected so tests run offline; anything
  that hits the real network is marked `@pytest.mark.network`.
- No lookahead: a decision at bar t may only use data from bars <= t (closed bars).

## Before calling a task done
```bash
ruff check . && ruff format --check . && mypy && pytest -m "not network"
cd frontend && npm run build
```

## Roadmap
- [x] P1 Data: Binance bulk ingestion, Parquet store, quality checks
- [x] P1.5 Platform: API, job runner, dashboard
- [ ] P2 Backtester: cost model (fees, spread, slippage), simulated fills,
      vectorised research engine, event-driven validation engine, metrics,
      Backtests page (equity, drawdown, cost attribution, blotter, run comparison)
- [ ] P3 Validation: walk-forward, parameter robustness, trial log, deflated Sharpe
- [ ] P4 Execution: ExecutionVenue adapters (sim, Binance testnet, live),
      reconciliation, risk limits, kill switch, alerting, parity tests
- [ ] P5 Perps: funding, basis, margin

## Working with Luke
- Be direct. Push back on ideas that would overfit or fool us.
- Explain design decisions briefly when making them. He needs to be able to
  defend every part of this codebase in an interview.
