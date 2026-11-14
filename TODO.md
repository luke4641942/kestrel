# To-do

The active list. Claude keeps it up to date in the same commit as the work (see CLAUDE.md).
Decisions and rules live in CLAUDE.md; results in `research/findings.md`.

## Waiting on Luke
- [ ] Read and freeze the draft note `trend-vol-target-btc-scorecard-walk-forward` (Strategies
      page), then commit it. Unlocks scorecard test 3 for trend-vol-target BTC.
- [ ] Decide on stops and take-profits (proposal in the 2026-11-14 conversation): one
      pre-registered trailing-stop variant on weekly momentum ETH and trend-vol-target BTC,
      or leave exits to the signals.
- [ ] Keep or delete `research/strategies/test/` (an empty "Test" idea from 4 Nov).

## Next
- [ ] Run the walk-forward once the note is frozen; record the result in decisions and findings.
- [ ] Scorecard for strategies without settings: weekly momentum has nothing to tune, so
      tests 3 (walk-forward) and 8 (robustness) can never run. Grade test 3 from fixed
      6-month windows and mark test 8 "not applicable".
- [ ] Charts page under More: any market and interval, indicator chips, a plain-English
      panel per indicator.
- [ ] P4 paper trading: sim venue, Binance testnet, reconciliation, risk limits, kill switch.
      First strategies: trend-vol-target BTC (pursue) and weekly momentum ETH (watch).

## Later
- [ ] Exclude tokenized stocks (AAPLBUSDT...) from the universe before any holdout run.
- [ ] Expectancy by conviction bucket (P2 leftover), once a strategy varies its sizing.
- [ ] Dashboard at phone width: the top menu and pages spill off the screen.
- [ ] HMM regime filter (backlog S2) as a strategy: needs a note that passes the entry bar.

## Done recently
- 2026-11-14 Weekly momentum ETH moved to "watch"; BTC and SOL stay dropped.
- 2026-11-14 Overview: the datasets line no longer lists every coin.
- 2026-11-14 Regimes page under More (HMM states, fitted as a job).
- 2026-11-14 HMM regime engine (`kestrel.regimes`, `kestrel regimes`).
- 2026-11-14 Scorecard graded on every backtest.
- 2026-11-14 Findings write-up for employers (`research/findings.md`).
- 2026-11-14 Rules reviewed; Rules page under More; `scripts/check.sh`.
