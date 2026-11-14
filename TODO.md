# To-do

The active list. Claude keeps it up to date in the same commit as the work (see CLAUDE.md).
Decisions and rules live in CLAUDE.md; results in `research/findings.md`.

## Waiting on Luke
- [ ] Binance Spot Testnet API keys (testnet.binance.vision) in environment variables, by
      P4 session 3. Never in code or the UI.
- [ ] Trend-vol-target BTC walk-forward note failed its pre-registered rule (2 of 7 windows,
      4 needed). Its status is still frozen: reject it, or keep it open? Your call.
- [ ] Weekly momentum ETH's notes (`holdout-weekly-momentum-eth`, `weekly-momentum-eth-scorecard`)
      are still open after its drop: reject them, or leave them? Your call.

## Next
- [ ] P4 paper trading, trend-vol-target BTC first (plan agreed 2026-11-14):
  - [x] S1 Shared rebalance step (`kestrel.execution.rebalance`); live feed of closed bars
        (`kestrel.data.live`, `kestrel update`). Done 2026-11-14.
  - [ ] S2 Paper runner on the simulated venue, with an append-only ledger; a day-by-day
        replay of 2025 reproduces the backtest exactly.
  - [ ] S3 Binance Spot Testnet venue (signed orders, exchange filters, order status).
  - [ ] S4 Reconciliation (halt on mismatch), risk limits, stale-data refusal (including a
        gap in the latest bars: `is_clean` doesn't count gaps), `kestrel kill`.
  - [ ] S5 Daily schedule with catch-up, the Trading page, a paper-vs-backtest tracking
        report. Then a paper-trading note fixes success criteria; run ~20 months.

## Later
- [ ] Exclude tokenized stocks (AAPLBUSDT...) from the universe before any holdout run.
- [ ] Expectancy by conviction bucket (P2 leftover), once a strategy varies its sizing.
- [ ] Dashboard at phone width: the top menu and pages spill off the screen.
- [ ] HMM regime filter (backlog S2) as a strategy: needs a note that passes the entry bar.

## Done recently
- 2026-11-14 P4 S1: shared rebalance step (five real runs identical) and `kestrel update`.
- 2026-11-14 Holdout tests shown on the Overview (read-only, `/api/holdout`).
- 2026-11-14 Labels (Luke): trend-vol-target BTC pursue; weekly momentum ETH drop.
- 2026-11-14 Holdout used once: trend-vol-target BTC passed, weekly momentum ETH failed.
- 2026-11-14 `kestrel holdout-test` built (synthetic tests only; the real holdout is untouched).
- 2026-11-14 Trend-vol-target BTC moved from pursue to watch (Luke). Nothing is on pursue.
- 2026-11-14 Trend-vol-target BTC walk-forward, window by window: beat holding in 2 of 7; fails test 3.
- 2026-11-14 Charts page under More, with a plain-English card per indicator.
- 2026-11-14 Crash brake tested on both survivors: made both worse; rejected.
- 2026-11-14 Weekly momentum ETH scorecard: passes all but luck (walk-forward 4 of 7); stays watch.
- 2026-11-14 Scorecard for strategies with nothing to tune (fixed-rule walk-forward, robustness n/a).
- 2026-11-14 Strategies cleaned out: seven tested notes rejected with their reasons; empty "Test" idea removed.
- 2026-11-14 Weekly momentum ETH moved to "watch"; BTC and SOL stay dropped.
- 2026-11-14 Overview: the datasets line no longer lists every coin.
- 2026-11-14 Regimes page under More (HMM states, fitted as a job).
- 2026-11-14 HMM regime engine (`kestrel.regimes`, `kestrel regimes`).
- 2026-11-14 Scorecard graded on every backtest.
- 2026-11-14 Findings write-up for employers (`research/findings.md`).
- 2026-11-14 Rules reviewed; Rules page under More; `scripts/check.sh`.
