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
- [ ] P5 Perps: funding, basis, margin. **Needs a rethink:** Binance perps aren't
      available to Australian retail. Options: ≤2x via an ASIC-licensed provider, or
      drop shorting/leverage. Decide before building.

## Decisions and parked ideas
Agreed with Luke; don't re-litigate without a reason.
- **Engine currency is USDT** (Binance spot quote). The dashboard converts to A$ for
  display only.
- **Holdout starts 2025-11-01 00:00 UTC (fixed, pre-registered 2026-10-31).** All
  research, tuning and validation use data before it. The engine must refuse to run on
  holdout data without an explicit flag and log every such run. It is used once, for a
  final verdict on a finished strategy. Never move this date after seeing results.
- **Long-only for now.** Strategies output target weights in [0, 1] per symbol (sum
  <= 1, rest is cash). Luke is open to shorting; it was planned for perps in P5, which
  now needs a rethink (see Leverage in Australia).
- **No-trade band: 2% of equity** (to live in `kestrel.toml`). Only rebalance when a
  target differs from the current position by more than the band, so tiny weight
  changes don't pay fees. Report how often the band suppressed a trade.
- **Benchmark: BTC buy-and-hold** over the same period, paying one entry's costs.
  Every result is shown against it and against cash.
- **Backtest conventions:** decide at bar t's close, fill at bar t+1's open; annualise
  with 365 days; risk-free rate 0; returns measured daily at 00:00 UTC for every
  strategy; each run stores config fingerprint, git commit and dirty flag; engine
  parity is tested on a large account (the vectorised engine can't model minimums).
- **Metrics.** Headline row on every backtest, in this order:
  return vs BTC buy-and-hold · Sharpe with 95% CI and probabilistic Sharpe (P(true
  SR > 0)) · net expectancy per trade with 95% CI · max drawdown + longest time
  underwater · break-even cost vs actual cost (bps per trade) · number of trades
  (with a warning when it is too few to trust).
  Detail view:
  - Returns: CAGR, vol, Sortino, Calmar (de-emphasised), calendar-year table, A$ P&L
    alongside %.
  - Market exposure: beta and correlation to BTC, exposure (% time invested), and a
    comparison against BTC held at the same average exposure.
  - Costs: turnover, cost drag (per year and share of gross), gross edge per trade vs
    cost per trade, break-even cost (cost at which net return reaches zero).
  - Tails: worst day, worst month, expected shortfall (95%, daily), skew, kurtosis
    (skew/kurtosis also feed P3's deflated Sharpe).
  - Profit per trade: expectancy (net, bps and A$), profit factor, payoff ratio, win
    rate only beside average win/loss, and profit concentration (share of net profit
    from the best 5 trades; flag when a handful of trades carry the result).
  - Statistical confidence: 95% CIs on expectancy, CAGR and Sharpe via block bootstrap
    (keeps crypto's autocorrelation), P(net profit > 0), and minimum track record
    length (how long paper trading must run before this Sharpe is credible; sets the
    P4 paper-trading duration). Always shown with the caveat that CIs assume a single
    trial until P3's deflated Sharpe corrects for every variant tried.
  - Conviction: strategies express conviction through target weight. Report net
    expectancy by entry-weight bucket to check that high conviction actually earns
    more; uncalibrated conviction just adds risk.
  - Trade record (blotter), per trade: entry/exit time and price, quantity, entry
    weight, gross P&L, fee/spread/impact, net P&L (USDT, A$, bps), holding period,
    max adverse and max favourable excursion.
  - Simulator effects: orders rejected (with reasons), trades the no-trade band skipped.
  - Tax (AU, information only): holding-period distribution and share of gains from
    positions held > 12 months (CGT discount). Not modelled in the engine.
  Treat max drawdown as a floor, not a forecast. Capacity metrics are out of scope.
- **Remaining P2 order:** ~~trading core, event-driven engine, core metrics~~ (done:
  `kestrel.strategy`, `kestrel.execution.venue`, `kestrel.backtest.engine`,
  `kestrel.backtest.metrics`, `kestrel backtest` CLI, ~~run storage + Backtests
  page~~ (`kestrel.backtest.runs`, `/api/backtests`) -> ~~vectorised engine with a
  parity test~~ (`kestrel.backtest.vectorised`, `tests/backtest/test_parity.py`; ~6x
  faster, agrees to <0.13% final equity; the fractional-weights case is the one that
  catches drift bugs, keep it) -> ~~stops, targets and risk-based sizing~~ (done: `BracketStrategy`,
  `ChannelBreakout` engine test, R metrics, trades in runs and on the Backtests page)
  -> remaining metrics (per-trade profit bootstrap CIs, BTC beta, break-even cost,
  tails, tax view, R-multiple distribution chart).
- **Stops, targets and risk-based sizing (P2, spot, no leverage).** Prompted by the
  "risk a little, target 3-4x" idea. Leverage doesn't change reward:risk; it only lets
  a tight stop carry a given risk, and tight stops are where costs dominate (22 bps
  round trip is 0.44R at a 0.5% stop). On 4h/daily bars stops are wide enough for spot.
  Build: intrabar stop/target exits from bar high/low, assuming the stop fills first
  when a bar touches both; size = risk% x equity / stop distance, capped at 100%;
  report results, expectancy and costs in R. A fixed R:R exit is a hypothesis to test
  in a research note, not an edge in itself.
- **Leverage in Australia (researched 2026-11-04).** Binance doesn't offer futures to
  Australian retail (licence cancelled April 2023 after misclassifying retail clients;
  Federal Court penalty reported March 2026). ASIC caps retail crypto CFD leverage at
  2:1. Never route around this via offshore exchanges or a wrong wholesale status.
  The only strategy is BuyAndHold (the benchmark) until the backtester is validated.
  The dashboard and API can never use the holdout; only the CLI's explicit flag can.
- **Config lives in `kestrel.toml`**, version-controlled. Every backtest run stores the
  full config it used, so old results keep their meaning. No global settings UI.
- **Strategies tab (built):** research notes in `research/strategies/<slug>/` (in the
  repo; Luke commits them). Backtesting a research strategy requires a frozen note;
  changes need a new version with a reason; rejection needs a reason and is final.
  `kestrel.research.run_target` is the one rule set for starting a backtest (CLI and
  API both call it). Strategy *code* lives in the repo, never in a browser editor.
  Pine Script / TradingView export was rejected: a second implementation would drift.
- **Research assistant (built):** Claude (`claude-opus-5-5`) sees the note, code and
  conversation only, never results, metrics or the trial log. Keep it that way: never
  add a results tool or pass run data into `ResearchAssistant.reply`.
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
