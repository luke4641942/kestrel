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
- Every change ships with tests, frontend included (Vitest + Testing Library; keep page
  logic in plain modules like `frontend/src/summary.ts` so it can be tested directly). HTTP is injected so tests run offline; anything
  that hits the real network is marked `@pytest.mark.network`.
- No lookahead: a decision at bar t may only use data from bars <= t (closed bars).

## Before calling a task done
```bash
ruff check . && ruff format --check . && mypy && pytest -m "not network"
cd frontend && npm test && npm run build
```

## Roadmap
- [x] P1 Data: Binance bulk ingestion, Parquet store, quality checks
- [x] P1.5 Platform: API, job runner, dashboard
- [ ] P2 Backtester: ~~cost model (fees, spread, slippage), simulated fills~~ (done:
      `kestrel.config`, `kestrel.backtest.costs`),
      vectorised research engine, event-driven validation engine, metrics,
      Backtests page (equity, drawdown, cost attribution, blotter, run comparison)
- [x] P3 Validation: deflated Sharpe (`kestrel.validation.deflated`, every run and
      hypothesis, `/api/validation`), walk-forward (`kestrel walk-forward`), parameter
      robustness (`kestrel robustness`), and a trial log that counts every setting a study
      tries (`kestrel.validation.log`, `data/validation/`, listed on the Overview)
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
- **Benchmark: buy-and-hold of the same market** (BTC for a BTC run, ETH for an ETH run)
  over the same period, paying one entry's costs. Labels must name the actual asset
  (`BacktestResult.asset`, `heldAsset()` in the dashboard); never hard-code "BTC".
  Every result is shown against it and against cash.
- **Backtest conventions:** decide at bar t's close, fill at bar t+1's open; annualise
  with 365 days; risk-free rate 0; returns measured daily at 00:00 UTC for every
  strategy; each run stores config fingerprint, git commit and dirty flag; engine
  parity is tested on a large account (the vectorised engine can't model minimums).
- **Metrics.** Headline row on every backtest, in this order:
  return vs buy-and-hold of the same market · Sharpe with 95% CI and probabilistic Sharpe (P(true
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
  -> ~~remaining metrics~~ (`kestrel.backtest.analysis`: block-bootstrap CIs, P(profit),
  min track record, break-even cost, BTC beta/correlation and exposure-matched BTC,
  tails, calendar years, payoff, profit without top 5 trades, FIFO tax view). Still to
  do: expectancy by conviction bucket, once a strategy varies its sizing.
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
- **First research strategy: `structure-pullback` — REJECTED 2026-11-05.**
  (`kestrel.strategies.structure`; note in `research/strategies/structure-pullback-craig-
  percoco-setup`.) Pre-registered test on BTC+ETH 4h, 2020-01..2025-10: 160 trades,
  expectancy +0.057R, P(>0) 63% < 90% required. Don't rescue it by tuning (a 3R target,
  other swing lengths, "BTC only"): those choices would come from seeing these results.
  A genuinely new reason means a new note version, and its runs count as new trials. The
  code stays as a worked example of a research strategy.
- **Chart-pattern batch (built 2026-11-05, not yet run).** Four research notes:
  level retest, rising trendline bounce, falling trendline breakout, falling wedge
  breakout (`kestrel.strategies.levels`, `.trendlines`, shared `.geometry`). Each names a
  comparison it must beat: kind `"control"` (`level-breakout`, `swing-pullback`), or the
  existing `channel-breakout` / `trendline-breakout`. Swings only count once confirmed
  (k bars later); that is the whole lookahead defence for lines, keep it. Tested on
  hand-built paths only. Four ideas tried at once means four trials: a single pass among
  them is weaker evidence than it looks until P3's deflated Sharpe.
- **Strategy backlog (2026-11-11): `research/backlog.md`** tracks every idea Luke has
  researched (S1-S4) and where it stands. Built and drafted, not yet run: trend following
  with volatility targeting (`kestrel.strategies.trend`, daily bars), Monday range as a
  raw-effect study first (`kestrel.studies.anchor_day`, `kestrel anchor-study`; a KILL
  rejects the note before any strategy run) then `monday-pullback`
  (`kestrel.strategies.weekday`), and S2-C pivot reversal without the HMM
  (`kestrel.strategies.pivots`). HMM work (S2, S3) waits by Luke's choice. Bracket
  strategies may now emit an `exit` column (time exits) and a null target.
- **Cross-sectional momentum (built 2026-11-11, not yet run).** Universe:
  `kestrel.data.universe` reads every USDT pair from Binance's archive, delisted ones
  included (`kestrel ingest-universe`, manifest in `data/universe/1d.json`). Binance
  reuses symbols (LUNAUSDT is two coins), so `kestrel.data.panel` numbers each coin's
  unbroken histories and nothing is computed across a gap. `kestrel.backtest.portfolio`
  holds several coins for one strategy; strategies return decisions (weekly), not daily
  targets. Each coin pays its own spread (`kestrel.backtest.spread`, Abdi-Ranaldo, 1 bp
  floor). Benchmark: BTC buy-and-hold. Tokenized stocks listed from June 2026
  (AAPLBUSDT...) aren't excluded yet; fix from Binance's metadata before any holdout run.
  This is a single strategy over many coins, not the parked multi-strategy allocator.
- **Results so far (2026-11-12).** Every hypothesis tested has been rejected: structure
  pullback, Monday range (study KILL), pivot reversal, the four chart patterns, trend +
  vol target (2020-25: BTC drawdown; 2017-18 unseen: cut drawdowns by more than half but
  lost money in the bear market), cross-sectional momentum (v2: -92% drawdown, Sharpe
  0.69; v1 void, faulty spread model). Channel breakout, the best-looking engine test,
  fails walk-forward on both coins. The consistent pattern: trend rules limit losses
  relative to holding but haven't beaten holding; reversal and pattern rules fail. Channel
  breakout is robust to its settings (a plateau) but fails walk-forward. With 73 trials,
  a Sharpe must beat about 1.26 to stand out from luck; the best so far (trend, 1.21/1.23)
  deflates to about 47%. Don't rescue any of
  these by tuning.
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
