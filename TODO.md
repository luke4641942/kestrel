# To-do

The active list. Claude keeps it up to date in the same commit as the work (see CLAUDE.md).
Decisions and rules live in CLAUDE.md; results in `research/findings.md`.

## Waiting on Luke
- [ ] Read and freeze `paper-trading-trend-vol-target-btc` (drafted 2026-11-14). Then:
      `kestrel paper open tvt-btc ... --venue testnet` (the note's rules), `kestrel paper
      schedule tvt-btc`, and load the job with the launchctl command it prints. The
      395-day test (90%, a 6-month failure-only check) starts at its first decision.
- [ ] Trend-vol-target BTC walk-forward note failed its pre-registered rule (2 of 7 windows,
      4 needed). Its status is still frozen: reject it, or keep it open? Your call.
- [ ] Weekly momentum ETH's notes (`holdout-weekly-momentum-eth`, `weekly-momentum-eth-scorecard`)
      are still open after its drop: reject them, or leave them? Your call.

## Next
- [ ] Delistings (Luke's idea, 2026-11-14): a raw-effect study before any strategy.
      Needs Binance delisting announcement dates (no official history: scrape the public
      announcements catalog, "Delistings"). Measure the path from announcement to removal
      and whether any rebound beats a small coin's costs, pre-holdout. Long-only can't take
      the short side; one study (Monero, 2024) found no rebound. Don't look at the delisted
      coins' prices before the study is written down.

## Later
- [ ] News-tone study (Luke's pick over Twitter, 2026-11-14): code built (`kestrel.data.news`,
      `kestrel signal-study news-tone`). Needs `kestrel ingest-news BTCUSDT ETHUSDT` first:
      GDELT allows ~1 request every 5-10s, so the 2017-25 history takes ~40+ minutes (run it in
      a spare terminal; it resumes if stopped). Then a note for Luke to freeze, then the study.
- [ ] BTC's regime turning volatile before ETH's, as an early ETH trigger (Luke's idea,
      2026-11-14). Only 11 such moments on daily regimes 2021-25: too few to test. Revisit
      only if the MACD study finds a direction, with hourly regimes for more events.
- [ ] Exclude tokenized stocks (AAPLBUSDT...) from the universe before any holdout run.
- [ ] Expectancy by conviction bucket (P2 leftover), once a strategy varies its sizing.
- [ ] Dashboard at phone width: the top menu and pages spill off the screen.
- [ ] HMM regime filter (backlog S2) as a strategy: needs a note that passes the entry bar.

## Done recently
- 2026-11-14 Attention and extreme-fear studies: both KILL; rejected (Luke).
- 2026-11-14 P4 paper trading built: sessions S1-S5 (details in CLAUDE.md's roadmap).
- 2026-11-14 volatile-macd backtested: fails the scorecard on BTC and ETH; rejected (Luke).
- 2026-11-14 Volatile-spell direction study: TREND (spread +3.70%/week, P 98%, 75 weeks).
- 2026-11-14 Regime filter on trend-vol-target: failed (Sharpe 0.64 vs 0.69); rejected (Luke).
- 2026-11-14 P4 S5: catch-up, tracking, schedule, Trading page; paper-trading note drafted.
- 2026-11-14 P4 S4: safety layer; a real kill-switch drill on the testnet.
- 2026-11-14 P4 S3: Binance Spot Testnet venue; a real testnet order filled end to end.
- 2026-11-14 P4 S2: paper runner on the simulated venue; replay identical to the backtest.
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
