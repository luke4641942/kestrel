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
- **Forex (P6, Luke 2026-11-23): research first, its own market.** Long and short, through
  an ASIC-licensed CFD broker (retail leverage capped by ASIC); costed as spread plus
  overnight financing. The spot, long-only rule above stays for crypto. No forex trading
  (paper or live) before a pass, and not before Luke has checked the Swyftx employee trading
  policy. Futures are research-only and parked until the account is far bigger.
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

## To-do list
`TODO.md` (repo root) is the active to-do list Luke reads in the IDE. Update it in the same
change whenever a task is started, finished, added or parked, and move finished items to
"Done recently" with the date. Keep it short: what's next, who it's waiting on.

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
- [x] Charts page (its own main tab since 2026-11-14): any market, bar size and range, the indicator chips, and a
      plain-English card per indicator switched on (`Preset.reading`)
- [x] P4 Execution (built 2026-11-14): sim and Binance testnet venues, the paper runner,
      reconciliation, risk limits, kill switch, tracking against the model, a launchd
      schedule, the Trading page. Left: a live venue (real money: not before the paper test
      says so). Alerts (`kestrel.execution.alerts`, 2026-11-22): `paper run --alerts` (the
      launchd jobs pass it) sends a macOS notification for a new halt, failed run, falling
      behind, missed day or trade; sent keys in each account's `alerts.jsonl`. Plan and progress in TODO.md. The
      backtest and the paper runner share one rebalance step (`kestrel.execution.rebalance`);
      the latest closed bars come from `kestrel.data.live` (`kestrel update`). The paper
      runner (`kestrel.execution.paper`) handles each closed bar like the engine (mark, decide,
      trade at the next open) and writes an append-only ledger in `data/paper/<account>/`; a
      day-by-day replay must reproduce the backtest exactly (`tests/test_paper.py`).
      `kestrel.execution.binance` is the Binance venue (Spot Testnet now): an account trades
      its own allocation, accounted from real fills; keys only from `KESTREL_TESTNET_KEY` /
      `KESTREL_TESTNET_SECRET`, never logged or stored. Testnet prices aren't the market's.
      Safety (`kestrel.execution.safety`, `[risk]` in kestrel.toml): breaches and failed
      reconciliations halt an account; only `kestrel paper resume --reason` lifts a halt;
      stale data stops a run without halting; `kestrel kill` sells to cash and halts.
      A live account that was behind records missed days (no trade) and trades the latest.
      `kestrel.execution.tracking` compares an account with its sim replay (decisions must
      match). A basket strategy (G1) trades through the portfolio engine's own day step
      (`kestrel.execution.basket`, `paper.run_basket`); its Binance venue holds a fixed coin set,
      and reconciliation adds up every testnet account (they share one exchange account). `kestrel paper schedule` writes a launchd job (keys from ~/.zshrc at run time).
      A run refreshes every market a strategy's features read (`kestrel.data.features
      .markets_for`: BTC for `btc_close`) and refuses to decide a bar with a missing input.
      The dashboard's Trading page is read-only; every action is a CLI command. Running
      (TODO.md): trend-vol-target on BTC, ETH, SOL, btc-regime-trend on ETH, SOL, G1
      (btc-rotation-8, one basket account, rot-g1), and stablecoin-vol-hold on BTC, ETH (watch,
      scs-btc and scs-eth), each its own testnet account. An
      always-on machine is parked (TODO.md).
- [x] Research tooling: the Research tab (decisions by family, the luck bar over time, the
      exploration experiment), the exploration split and confirmation run, cross-sectional
      studies, delisting studies, the minute-chart cost gate, the overlap report
      (`kestrel overlap`: how much saved runs are one bet).
- [ ] P6 Forex (started 2026-11-23; plan in TODO.md): rules (pools, holdout, universe),
      bid/ask daily data, an engine that takes signed and leveraged weights (margin,
      financing, AUD accounting; parity with the crypto engine when both are off), then the
      first note: trend following across the 10 pairs, with published outside evidence.
- Parked: P5 perps (see Parked), futures research (paid data; contracts too large for the
  account), multi-strategy portfolios, Settings page.

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
  9. Bracket strategies only: net expectancy per trade beats 95% of random-entry paths
     with the same stops, targets and sizing (`kestrel random-entry`,
     `kestrel.validation.control`; added 2026-11-15). n/a for weight strategies.
- **Independence (information only; Luke, 2026-11-21).** A new strategy's result also
  reports how much it overlaps the pursued strategies (`kestrel overlap`: correlation, in
  BTC's falls too, and BTC beta). It never decides a pass; it says whether a pass would be a
  second edge or more of the same bet (findings 16).
- **Decay check (information only; Luke, 2026-11-22).** After 90 paper days, an account's model
  replay is flagged if its Sharpe is below the 5th percentile of its own backtest's (same
  strategy, settings and market, 2020-01 to the holdout; circular block-bootstrapped paths of
  the same length): `kestrel.validation.decay`, `paper track`, the Trading page, an alert. A
  flag is a prompt to look; any downgrade waits for Luke. At 90 days the floor is near Sharpe
  -2.3, at 395 near -0.4: it catches collapse early, not slow fading.
- **Risk-managed holding** is a separate category for a strategy that hasn't passed but
  Luke may run because it smooths the ride. It is labelled "not a proven edge" and never
  counts as a pass.
- **Decisions** (`research/decisions.toml`, one per strategy and market): `pursue` means
  carry on to paper trading, not "passed"; `watch`; `needs-shorting` (a hypothetical shorting study that passed: never traded until a
  licensed retail route exists); `drop` (final). Pursued (2026-11-16):
  trend-vol-target on BTC, ETH and SOL (each beat exposure-matched holding, failed luck and
  walk-forward, and passed its holdout), and btc-regime-trend on ETH and SOL (passed its
  confirmation on unsearched coins and every scorecard test but luck), and btc-rotation-8
  (G1: passed a holdout year on unseen coins; fails luck and walk-forward on its own coins,
  2018-25, whose coin list is survivorship-biased). The holdout is spent
  for BTC, ETH and SOL. A rejected *note* stays rejected; the decision file is the separate
  "what do we do with it" layer. A finished note closes as `validated` or `paper`.
- **Entry bar for a new idea.** Its note states who pays you and why (an economic reason,
  not a chart pattern), outside evidence (published or out-of-sample elsewhere), that it
  is not a variant of a rejected family (pullbacks, chart patterns, reversals), and how
  many runs it will use. More history (2017+) is preferred to more ideas.
- **Trials.** Every setting tried counts (`kestrel.validation.log`, `data/validation/`).
  Runs in the exploration half are not trials (see Exploration experiment).
  863 trials by 2026-11-21 (robustness and walk-forward grids count every setting), luck
  bar Sharpe 2.28; the Research tab shows how it moved. The bar depends on how spread out
  the trials' Sharpes are as well as their number, so very bad strategies raise it too.
  Confirmation runs have their own pool. It may be that nothing passes; that is an
  acceptable result. Scripts reading runs use `RunStore("data")` (it adds `runs/`).
- **Exploration experiment (started 2026-11-16, kept after round 1; Luke's call;
  revert by deleting this section and its line under Trials).**
  The coins in research/exploration/split.toml are split, fixed before any look:
  - Exploration half: anything goes (grid searches, mining, machine learning,
    overfitting). Runs are saved apart (data/exploration/), are not trials, and are
    never evidence: nothing found there is a result.
  - Confirmation half: locked like the holdout. The engine refuses it except
    through a frozen confirmation note.
  - At most 3 ideas graduate, each as a frozen note with its exact rule and a
    who-pays story (outside evidence not required; a rejected family is allowed
    but disclosed). Each is run once on the confirmation half under the full
    scorecard, with its own trial count (only confirmation trials), not the main
    luck bar.
  - Pre-registered test of the method: it works if at least one graduate beats
    holding at the same exposure on most confirmation coins and passes walk-forward.
  - One round only. Then Luke decides: keep the method, or revert. Round 1 (2026-11-16):
    btc-regime-trend PASSED (83% of coins, 55% of windows), crash-rebound FAILED; Luke
    kept the method. The confirmation half is now spent: a second round needs fresh,
    unsearched data (new listings, or time going forward) and its own split.
  - The holdout rule is unchanged: a graduate that passes still needs its own
    holdout test before it could be pursued.
- **Don't rescue a strategy by tuning.** A setting chosen after seeing results is a new
  trial. A genuinely new reason means a new note version, and its runs count.
- **Markets on the dashboard (Luke, 2026-11-23).** A switcher in the top bar (All, Crypto,
  Forex, Futures parked) filters Research, Strategies and Backtests; pages built for crypto
  only say so under another market. A strategy's market is read from its key
  (`kestrel.markets`: `fx:` is forex), never written twice. The Research tab's grid lays
  families against markets, each column with its own luck bar and every pool's total; a
  good cell nobody predicted is a lead for a new note, never a finding.
- **Trial pools: one per market (Luke, 2026-11-23).** Crypto (every trial up to now),
  forex, and futures each have their own trial log and luck bar; a pass is judged only
  against its own pool. Fixed before any forex or futures result; pools are never merged
  or split after a look. Every report of a luck bar also states the total trials across
  all pools. An idea carried over from crypto (trend-vol-target, say) is a new trial in
  the new pool, and its note says where its shape came from.
- **Forex holdout starts 2023-01-01 00:00 UTC (fixed, pre-registered 2026-11-23 before any
  forex data was ingested; never move it).** Research uses forex data before it. Disclosed:
  trend-vol-target already ran on Binance's EURUSDT and GBPUSDT (2020-01 to 2025-10, Sharpe
  -0.68 and -0.88, dropped), so EUR/USD and GBP/USD from 2023 have been seen once by a trend
  rule. A forex holdout verdict for a trend strategy is reported with and without those two
  pairs, and must pass both ways. Universe fixed now: EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD,
  AUDUSD, NZDUSD, EURGBP, EURJPY, GBPJPY; adding a pair later is a note, never a rescue.
- **Holdout starts 2025-11-01 00:00 UTC (fixed, pre-registered 2026-10-31).** All
  research, tuning and validation use data before it. The engine refuses holdout data
  without the CLI's explicit flag and logs every such run; the dashboard and API can never
  use it. Used once, for a final verdict on a finished strategy. Never move this date.
  The path for that verdict is `kestrel holdout-test` (`kestrel.validation.holdout`): a
  frozen note only, a warm-up before 2025-11, the holdout year scored against holding
  (returns at the same exposure in a falling year), recorded once per note in
  `data/holdout/`, never a trial.
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
- **Forex engine** (`kestrel.backtest.fx_engine`, P6, 2026-11-23): its own engine, so
  shorting and leverage never touch the crypto engine the paper accounts use. Signed
  weights per pair, gross at most `[fx] max_gross_leverage`; fills at the bid/ask from the
  data; marks at the mid; financing per calendar night (rate difference less
  `financing_markup_pct`, 1.0% measured from OANDA, both sides; rates are an input, never
  defaulted: `kestrel.data.rates`, OECD 3-month interbank, the month's own rate for
  charging, `known_rate` (month before last) for any decision); P&L in AUD
  through the pairs' own mids; a holiday is neither traded nor marked; a wipe-out stops
  the run. Parity-tested against the crypto engine (long only, unlevered). Weekend candles
  are dropped (`fx.trading_days`, Luke's decision 2026-11-23). Forex strategies have `fx:`
  keys (`kestrel.fx_strategy`, lookahead-checked like crypto's) and run only through
  `kestrel fx-backtest` from a frozen note; runs live in `data/fx/runs/` (never with crypto
  runs), metrics on calendar days (equity carried over weekends) so annualising stays 365,
  and count in the forex pool (`validation.deflated.pool_of`).
- **Stops and sizing:** intrabar stop/target exits from the bar's high/low, the stop
  assumed first when a bar touches both; size = risk% x equity / stop distance, capped at
  100%; results in R. Bracket strategies may emit an `exit` column and a null target, and
  a `limit` with `valid_bars` for a resting buy limit (fills when a bar reaches it, at the
  open if it opens below; on its fill bar only the stop can end the trade). A fixed R:R
  exit is a hypothesis to test, not an edge.
- **Universe:** `kestrel.data.universe` reads every USDT pair, delisted ones included.
  Binance reuses symbols (LUNAUSDT is two coins), so `kestrel.data.panel` numbers each
  coin's unbroken histories and nothing is computed across a gap. Each coin without its own
  `[instruments]` entry pays a half-spread band set by its 30-day median daily volume
  (`kestrel.backtest.spread`, `[universe] spread_tiers`; on sub-daily bars, the complete
  days before; Abdi-Ranaldo was tried and dropped as too noisy). Tokenized stocks (AAPLBUSDT...,
  from June 2026) are excluded by Binance's `bStocks` product tag, read at ingest; delisted
  BUSDT pairs the live list can't classify are reported, not guessed.
- **Regimes** (`kestrel.regimes`): Gaussian HMM written here on numpy (no hmmlearn: its
  `predict`/`predict_proba` smooth over the whole sequence). Probabilities are always
  *filtered* (inputs up to t only); refitted monthly on the 4 years before, states sorted
  calmest first after every fit. Fixed `PRESET` (3 states; log return and log 20-day
  volatility). Other inputs are compared by out-of-sample log-likelihood, never by trading
  results. A strategy that trades on states needs its own note and the scorecard.
  `FAST_PRESET` is findings 23's 4h model (20 *bars* of volatility, a 1-year window, 3
  restarts), stored apart (`kestrel regimes <COIN> --interval 4h --save`, `data/regimes/4h/`)
  and read on daily bars as `regime_volatile_4h` (the 4h bar closing at the day's close).
- **Features** (`kestrel.data.features`: Fear & Greed, funding, BTC's close, the volatile
  regime, stablecoin supply) are joined on `open_time` and must be known by the bar's close.
- **Metrics.** Headline row, in order: return vs buy-and-hold of the same market · Sharpe
  with 95% CI and P(true SR > 0) · net expectancy per trade with 95% CI · max drawdown and
  longest time underwater · break-even vs actual cost (bps) · number of trades, warning
  when too few. The detail view (`kestrel.backtest.analysis`) covers returns, market
  exposure, costs, tails, profit per trade, block-bootstrap confidence, start dates, the
  blotter and an information-only AU tax view. CIs assume a single trial; say so. Max
  drawdown is a floor, not a forecast. Capacity is out of scope.

## Parked
- **Hypothetical shorting studies** (Luke, 2026-11-16) are allowed: frozen notes, counted
  trials, costed as a 2:1 CFD (spread plus financing) and as a Binance perp (funding). A
  result that would be pursue or watch is recorded as `needs-shorting`: never traded until
  a licensed retail route exists. List in TODO.md.
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
- **Never reject a research note, or drop or downgrade a strategy's decision, without Luke's
  explicit say-so** (2026-11-14). Run what a frozen note specifies, record the result
  plainly (decisions reasons, findings, scorecard), say what the note's rule implies, and
  ask. A pre-registered result is never softened in the record; only the status change waits.
- Be direct. Push back on ideas that would overfit or fool us.
- Explain design decisions briefly when making them. He needs to be able to
  defend every part of this codebase in an interview.
