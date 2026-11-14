# Kestrel: notes for Claude

Systematic crypto trading platform, built to be shown to quant employers. Quality
and honesty of results matter more than speed. Read README.md for architecture,
`research/findings.md` for what the research has found, and `research/decisions.toml`
for where each strategy stands.

This file is the rulebook. Results go in `research/findings.md`, not here.

## Goals and constraints
- Spot only, long-only (Binance). Strategies output target weights in [0, 1] per symbol
  (sum <= 1, rest is cash).
- Account ~A$500 (~330 USDT). Research at the real size. Binance refuses orders under
  5 USDT, so a strategy holds at most 3 coins at once. Revisit if the account grows.
- Low turnover (4h/daily horizons). Fees dominate at this size; never present a result
  without costs included.
- A strategy must behave identically in backtest, paper and live. Strategies talk to an
  `ExecutionVenue` interface, never to an exchange directly.
- Luke works in ~45-minute weekday sessions and longer on days off. Size tasks so one
  session ends with something merged, tested and working.

## Layers (dependencies point downward only)
1. `frontend/` React + TypeScript dashboard. Talks only to the API.
2. `kestrel.api` FastAPI adapter + JobManager. No business logic here.
3. Engine: `kestrel.data`, `kestrel.backtest`, `kestrel.validation`, `kestrel.strategies`,
   `kestrel.execution`. The engine must never import from `kestrel.api`.

Long-running operations (ingest, backtests, validation) run as jobs via `JobManager`,
reporting progress and logs and checking cancellation.

## Conventions
- Python 3.11+, polars (not pandas), strict mypy, ruff. Type everything.
- Timestamps: tz-aware UTC, microseconds. `open_time` is a bar's identity.
- Data problems are reported, never silently repaired.
- Every change ships with tests, frontend included (Vitest + Testing Library; keep page
  logic in plain modules like `frontend/src/summary.ts` so it can be tested directly).
  HTTP is injected so tests run offline; anything that hits the real network is marked
  `@pytest.mark.network`.
- No lookahead: a decision at bar t may only use data from bars <= t (closed bars).
  Swings only count once confirmed (k bars later); that is the whole lookahead defence
  for lines. Indicators are checked by recomputing on truncated histories.
- Config lives in `kestrel.toml`, version-controlled. Every backtest run stores the full
  config, its fingerprint, the git commit and a dirty flag. No global settings UI.
- After each step: commit, push, and watch CI (`gh run watch`). Luke commits research
  notes in `research/strategies/` himself unless he asks otherwise.

## Before calling a task done
```bash
scripts/check.sh   # ruff, format, mypy, pytest, frontend tests, build; stops at the first failure
```
Never pipe check output through `tail` or similar inside an `&&` chain: it hides failures.

## Roadmap
- [x] P1 Data: Binance bulk ingestion, Parquet store, quality checks
- [x] P1.5 Platform: API, job runner, dashboard
- [x] P2 Backtester: cost model, event-driven and vectorised engines (parity-tested),
      stops/targets and risk sizing, metrics and analysis, Backtests page. Left: expectancy
      by conviction bucket, once a strategy varies its sizing.
- [x] P3 Validation: deflated Sharpe, walk-forward, parameter robustness, trial log
- [x] The scorecard (below) in code: `kestrel.validation.scorecard` grades every run from
      its metrics, analysis, deflated Sharpe and studies on the same market and period;
      shown on each backtest. Walk-forward studies record each test window against holding.
- [x] HMM regimes (backlog S2/S3): `kestrel.regimes`, `kestrel regimes`, and the Regimes
      page under More (fitted as a job, stored in `data/regimes/`). Describes; never trades.
      A strategy using states (S2 as a filter) is a research note and the scorecard.
- [ ] Charts page under More: any market and interval, indicator chips, a plain-English
      panel per indicator (reuse CandleChart, IndicatorPicker, `/api/indicators`)
- [ ] P4 Execution: ExecutionVenue adapters (sim, Binance testnet, live), reconciliation,
      risk limits, kill switch, alerting, parity tests. First run: trend-vol-target on BTC
      as risk-managed holding, to prove the plumbing.
- Parked: P5 perps (see Parked), multi-strategy portfolios, Settings page.

## Research rules (reviewed 2026-11-14)
- **What a pass means.** A strategy passes only if it beats holding the same coin *at the
  same average exposure* on Sharpe, after costs, and its deflated Sharpe clears the bar.
  Lower drawdown alone is not an edge: holding less of the coin gives it for free.
  Drawdown is a limit, not a goal.
- **The scorecard.** Every strategy, on pre-holdout data. A note may add tests, never
  remove one:
  1. Sharpe beats exposure-matched holding of the same coin, after costs.
  2. Deflated Sharpe >= 95% (counts every trial).
  3. Walk-forward: most test windows beat exposure-matched holding.
  4. Beats exposure-matched holding in at least half of the 2-year start windows.
  5. Break-even cost >= 2x the actual cost.
  6. At least 30 trades.
  7. Max drawdown <= 50%.
  8. Neighbouring settings also pass test 1 (a plateau, not a peak).
- **Risk-managed holding** is a separate category for a strategy that hasn't passed but
  Luke may run because it smooths the ride. It is labelled "not a proven edge" and never
  counts as a pass.
- **Decisions** (`research/decisions.toml`, one per strategy and market): `pursue` means
  carry on to paper trading, not "passed"; `watch`; `drop` (final). Trend-vol-target on
  BTC is the only pursue, as risk-managed holding: it beats exposure-matched holding
  (Sharpe 1.21 vs 1.07) but deflates to 28% at 172 trials.
- **Entry bar for a new idea.** Its note states who pays you and why (an economic reason,
  not a chart pattern), outside evidence (published or out-of-sample elsewhere), that it
  is not a variant of a rejected family (pullbacks, chart patterns, reversals), and how
  many runs it will use. More history (2017+) is preferred to more ideas.
- **Trials.** Every setting tried counts (`kestrel.validation.log`, `data/validation/`).
  172 trials by 2026-11-14 (robustness grids count every setting), luck bar Sharpe ~1.45.
  The bar depends on how spread out the trials' Sharpes are as well as their number. It may
  be that nothing passes; that is an acceptable result.
- **Don't rescue a strategy by tuning.** A setting chosen after seeing results is a new
  trial. A genuinely new reason means a new note version, and its runs count.
- **Holdout starts 2025-11-01 00:00 UTC (fixed, pre-registered 2026-10-31).** All
  research, tuning and validation use data before it. The engine refuses holdout data
  without the CLI's explicit flag and logs every such run; the dashboard and API can never
  use it. Used once, for a final verdict on a finished strategy. Never move this date.
- **Notes.** Research notes live in `research/strategies/<slug>/`. Backtesting a research
  strategy requires a frozen note; changes need a new version with a reason; rejection
  needs a reason and is final. `kestrel.research.run_target` is the one rule set for
  starting a backtest (CLI and API both call it). Strategy code lives in the repo, never
  in a browser editor. Pine Script export was rejected: a second implementation drifts.
- **Research assistant** (Claude, `claude-opus-5-5`) sees the note, code and conversation
  only, never results, metrics or the trial log. Never add a results tool or pass run data
  into `ResearchAssistant.reply`.
- **Indicators** (`kestrel.indicators`): one tested implementation each; new strategies
  use them rather than their own copies. The dashboard draws a fixed menu (`PRESETS`) at
  textbook settings. No UI to change lengths and no automatic combination search.

## Engine decisions
Agreed with Luke; don't re-litigate without a reason.
- **Engine currency is USDT** (Binance spot quote). The dashboard converts to A$ for
  display only.
- **No-trade band: 2% of equity** (`kestrel.toml`). Only rebalance when a target differs
  from the position by more than the band; report how often it suppressed a trade.
- **Benchmark: buy-and-hold of the same market** over the same period, paying one entry's
  costs, plus the same market at the strategy's average exposure. Labels name the actual
  asset (`BacktestResult.asset`, `heldAsset()`); never hard-code "BTC". Universe
  strategies are judged against the `large-basket` control, never just BTC.
- **Backtest conventions:** decide at bar t's close, fill at bar t+1's open; annualise
  with 365 days; risk-free rate 0; returns measured daily at 00:00 UTC; engine parity is
  tested on a large account (the vectorised engine can't model minimums). Keep the
  fractional-weights parity case: it catches drift bugs.
- **Stops and sizing:** intrabar stop/target exits from the bar's high/low, the stop
  assumed first when a bar touches both; size = risk% x equity / stop distance, capped at
  100%; results in R. Bracket strategies may emit an `exit` column and a null target. A
  fixed R:R exit is a hypothesis to test, not an edge.
- **Universe:** `kestrel.data.universe` reads every USDT pair, delisted ones included.
  Binance reuses symbols (LUNAUSDT is two coins), so `kestrel.data.panel` numbers each
  coin's unbroken histories and nothing is computed across a gap. Each coin pays its own
  spread (`kestrel.backtest.spread`, Abdi-Ranaldo, 1 bp floor). Tokenized stocks listed
  from June 2026 (AAPLBUSDT...) aren't excluded yet; fix from Binance's metadata before
  any holdout run.
- **Regimes** (`kestrel.regimes`): Gaussian HMM written here on numpy (no hmmlearn: its
  `predict`/`predict_proba` smooth over the whole sequence). Probabilities are always
  *filtered* (inputs up to t only); refitted monthly on the 4 years before, states sorted
  calmest first after every fit. Fixed `PRESET` (3 states; log return and log 20-day
  volatility). Other inputs are compared by out-of-sample log-likelihood, never by trading
  results. A strategy that trades on states needs its own note and the scorecard.
- **Features** (`kestrel.data.features`: Fear & Greed, funding, BTC's close) are joined on
  `open_time` and must be known by the bar's close.
- **Metrics.** Headline row, in order: return vs buy-and-hold of the same market · Sharpe
  with 95% CI and P(true SR > 0) · net expectancy per trade with 95% CI · max drawdown and
  longest time underwater · break-even vs actual cost (bps) · number of trades, warning
  when too few. The detail view (`kestrel.backtest.analysis`) covers returns, market
  exposure, costs, tails, profit per trade, block-bootstrap confidence, start dates, the
  blotter and an information-only AU tax view. CIs assume a single trial; say so. Max
  drawdown is a floor, not a forecast. Capacity is out of scope.

## Parked
- **P5 perps.** Binance doesn't offer futures to Australian retail (licence cancelled April
  2023; Federal Court penalty reported March 2026); ASIC caps retail crypto CFD leverage at
  2:1. Revisit only if a spot strategy passes and shorting would clearly help. Never route
  around this via offshore exchanges or a wrong wholesale status.
- **Multi-strategy portfolios.** Strategies emit target positions, never orders; an
  allocator would own the wallet and net orders before the `ExecutionVenue`. Every
  strategy/weight combination tried counts as a trial; weights come from a rule.
- **Settings page (P4+).** Only risk limits (audited edits) and exchange connection
  status. Live balances are read from the exchange. API keys stay in env vars or the
  keychain, never in the UI.

## Working with Luke
- Be direct. Push back on ideas that would overfit or fool us.
- Explain design decisions briefly when making them. He needs to be able to
  defend every part of this codebase in an interview.
