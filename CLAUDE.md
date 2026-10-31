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
- [ ] P2 Backtester: ~~cost model (fees, spread, slippage), simulated fills~~ (done:
      `kestrel.config`, `kestrel.backtest.costs`),
      vectorised research engine, event-driven validation engine, metrics,
      Backtests page (equity, drawdown, cost attribution, blotter, run comparison)
- [ ] P3 Validation: walk-forward, parameter robustness, trial log, deflated Sharpe
- [ ] P4 Execution: ExecutionVenue adapters (sim, Binance testnet, live),
      reconciliation, risk limits, kill switch, alerting, parity tests
- [ ] P5 Perps: funding, basis, margin

## Decisions and parked ideas
Agreed with Luke; don't re-litigate without a reason.
- **Engine currency is USDT** (Binance spot quote). The dashboard converts to A$ for
  display only.
- **Holdout starts 2025-11-01 00:00 UTC (fixed, pre-registered 2026-10-31).** All
  research, tuning and validation use data before it. The engine must refuse to run on
  holdout data without an explicit flag and log every such run. It is used once, for a
  final verdict on a finished strategy. Never move this date after seeing results.
- **Long-only for now.** Strategies output target weights in [0, 1] per symbol (sum
  <= 1, rest is cash). Luke is open to shorting; it arrives with perps in P5.
- **No-trade band: 2% of equity** (to live in `kestrel.toml`). Only rebalance when a
  target differs from the current position by more than the band, so tiny weight
  changes don't pay fees. Report how often the band suppressed a trade.
- **Benchmark: BTC buy-and-hold** over the same period, paying one entry's costs.
  Every result is shown against it and against cash.
- **Backtest conventions:** decide at bar t's close, fill at bar t+1's open; annualise
  with 365 days; risk-free rate 0; returns measured daily at 00:00 UTC for every
  strategy; each run stores config fingerprint, git commit and dirty flag; engine
  parity is tested on a large account (the vectorised engine can't model minimums).
- **Metrics:** CAGR, vol, Sharpe, Sortino, max drawdown + duration, Calmar, turnover,
  cost drag (per year and as a share of gross), exposure, trade count, vs benchmark;
  also skew and kurtosis, which P3's deflated Sharpe needs.
- **Remaining P2 order:** trading core (`ExecutionVenue`, sim venue, target
  positions) -> vectorised engine -> event-driven engine -> metrics -> Backtests page.
- **Config lives in `kestrel.toml`**, version-controlled. Every backtest run stores the
  full config it used, so old results keep their meaning. No global settings UI.
- **Parked, P3+: multi-strategy portfolios.** Strategies emit *target positions*, never
  orders; an allocator owns the wallet, resolves conflicts and nets orders before the
  `ExecutionVenue`. Build P2 around this so single-strategy is a portfolio of one.
  Open questions: fixed sleeves vs shared pool; Backtests mode vs separate tab.
  Every strategy/weight combination tried counts as a trial; weights come from a rule.
- **Parked, P4+: Settings page.** Only risk limits (audited edits) and exchange
  connection status. Live balances are read from the exchange, never typed in.
  API keys stay in env vars or the keychain, never in the UI.

## Working with Luke
- Be direct. Push back on ideas that would overfit or fool us.
- Explain design decisions briefly when making them. He needs to be able to
  defend every part of this codebase in an interview.
