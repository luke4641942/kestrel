# To-do

The active list. Claude keeps it up to date in the same commit as the work (see CLAUDE.md).
Decisions and rules live in CLAUDE.md; results in `research/findings.md`.

## Top
- [ ] Cost gate (from Luke's YouTube spec, 2026-11-15): before building any 1m/5m strategy,
      measure on BTC/ETH/SOL 1m and 5m bars how wide FVG/zone stops are in bps and what a
      22 bps round trip costs in R. Ingest 1m bars first (12+ months per coin). Decides
      whether S2/S3 below can pay their fees at 330 USDT at all.
- [ ] Random-entry control for every bracket strategy (from the same spec): run the same
      stops, targets and sizing on random entry times and directions (long only here), and
      require a strategy to beat it. Tests whether an edge is more than the payoff shape
      (e.g. 4R wins against 1R losses). Becomes a scorecard input for bracket strategies.

## YouTube strategies (Luke's test spec, 2026-11-15): on hold behind the cost gate
Not ruled out, not yet built. Each waits for the cost gate's answer and Luke's decision on
the entry-bar conflict below.
Source: "YouTube Trading Strategies — Test Spec" (13 videos, 3 strategies). All three must be
adapted to Kestrel's rules: spot, long-only, 330 USDT, Binance data, costs on every fill.
Each gets its own frozen note and the scorecard; every setting tried is a trial.
- [ ] 1. S2, CHoCH + FVG (Craig Percoco): NY open, 1m change of character, limit at the FVG
      midpoint, stop beyond FVG candle 1, 4R target. Long setups only. Most codeable.
      Spec defaults: pivot N 3, gap >= 0.3 x ATR(14, 1m), 30-min order expiry, 3 trades/day.
      Judged against the random-entry control above.
- [ ] 2. S3, market shift (Brad Goh): 1H bias / 15m zones / 5m sweep + body-close shift,
      enter the shift's zone, 2R or next zone, premium/discount filter. Reuses S2's swing,
      sweep and shift code. Long setups only; test only if S2's core holds up.
- [ ] 3. S1, Set and Forget (Alex): W/D/4H structure, weekly/daily zones with 3+ touches, 1H
      engulfing entry, 2R-4R. Forex in the original: adapt to crypto 4H/1H with ATR-based
      zone widths. Least codeable (discretionary swings and zones); last, if at all.
- Rules conflict to decide (Luke): CLAUDE.md's entry bar says a new idea mustn't be a variant
  of a rejected family, and these are structure/pullback/sweep-reversal setups. A Craig
  Percoco structure pullback was rejected on 2026-11-05 (+0.06R). Testing them means
  overriding the bar knowingly, with the trials counted.

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
