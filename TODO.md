# To-do

The active list. Claude keeps it up to date in the same commit as the work (see CLAUDE.md).
Decisions and rules live in CLAUDE.md; results in `research/findings.md`.

## YouTube batch 2 (Luke's test spec, 2026-11-15): outlined, nothing built
Nothing here is ruled out. Most items have a near-twin we've already tested and dropped
(named below). Testing one of those means knowingly overriding the entry bar (Luke's
call), and every run counts as a trial. The daily items don't need the cost gate. All are
spot, long-only, at most 3 coins, and judged by the scorecard against exposure-matched
holding.
- [ ] A3. Turn of the month, BTC 1d: hold from the last day's close to the first day's
      close; variant: the last 5 days plus the first 3. Compare with random days of the
      same count. A new family (calendar) with no twin in our records. The equity reason
      (month-end pay and fund flows) has no clear crypto version, so the note must give
      one. 2 trials.
- [ ] A1/A2/A4. RSI dip-buying, BTC and ETH 1d: RSI(2) < 10 or RSI(5) < 30. Exit when the
      close is above yesterday's high. Run with and without the 200-day filter, and with
      and without the scale-in.
      Twins: buy big down days (dropped: +21% vs +42% for BTC at the same exposure), Fear &
      Greed contrarian (dropped), extreme fear (KILL). The video's finding that "mean
      reversion survives" comes mostly from equities with upward drift, and it never
      compares against holding at the same exposure. Keep it to 8 trials or fewer.
- [ ] A5/A6. Moving-average timing, BTC/ETH/cash 1d: hold each coin while its 3-month MA
      is above its 10-month MA, equal weight, rebalanced monthly. Twins: trend-vol-target
      (already uses MA votes; pursue), BTC/ETH rotation (dropped). A5 needs three markets
      (S&P, Dow, utilities) and has no clean crypto version.
- [ ] B1/B2. Breakouts on a coin basket: a 100-day 3 SD Bollinger band, or 20% above the
      50-day low. BTC above its 200-day as the filter, lowest volatility first, ATR stop.
      Twins: channel breakout (dropped), trend across a 10-coin basket (dropped). Its 10
      slots must shrink to 3.
- [ ] Cross-sectional momentum, long the top 3 coins by 1-3 month return. Twin:
      cross-sectional momentum top 5 (dropped, -92% drawdown). The video's version is
      long-short; we can only take the long half.
- [ ] C. Order flow (Fabio Valentino), BTC/ETH 1m-5m: delta, cumulative delta and the
      session volume profile, built from Binance spot aggTrades (they record which side
      was the aggressor). Model 2 first: a failed breakout returning to the point of
      control. A new family. Needs trade-level data, not bars. Batch 1's cost gate found
      1m stops of 6-19 bp against a 22 bp round trip; gate C's own stops before building.
- D. Earnings IV-crush calendars: out of scope (US equity options). Not on the list.
- [ ] Validation ideas from the 9,000-backtest video. We already have walk-forward, costs,
      drawdown, minimum trades, deflated Sharpe, robustness and a bootstrap. Not yet built:
      (1) out-of-sample Sharpe at least half of in-sample; (2) reshuffle a run's trades
      1,000 times and show drawdown percentiles; (3) results by calendar year. Adding (2)
      and (3) to the detail view is information only. Making any of them a scorecard test
      changes the rules (Luke). The video's own bar (Sharpe > 0.5, no comparison with
      holding) is weaker than ours on the edge test, so ours stays.

## YouTube strategies batch 1 (Luke's test spec, 2026-11-15): cost gate FAILED, Luke to decide
Not ruled out, not built. The cost gate (`kestrel cost-gate`, 2024-11 to 2025-10) measured
S2's stops: median 6-19 bp on 1m and 15-45 bp on 5m, so a 22 bp round trip costs 0.5-3.9R
a trade. FAILS on 17 of 18 rows; SOL 5m at the NY open is MARGINAL (0.49R, break-even win
rate 30% at 4R). Luke's call: drop S2/S3, or test SOL 5m NY only (overriding the entry bar).
S1 (4H/1H) has wider stops and wasn't gated.
Source: "YouTube Trading Strategies — Test Spec" (13 videos, 3 strategies). All three must be
adapted to Kestrel's rules: spot, long-only, 330 USDT, Binance data, costs on every fill.
Each gets its own frozen note and the scorecard; every setting tried is a trial.
- [ ] 1. S2, CHoCH + FVG (Craig Percoco): NY open, 1m change of character, limit at the FVG
      midpoint, stop beyond FVG candle 1, 4R target. Long setups only. Most codeable.
      Spec defaults: pivot N 3, gap >= 0.3 x ATR(14, 1m), 30-min order expiry, 3 trades/day.
      Would be judged against the random-entry control (scorecard test 9).
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
- 2026-11-15 Random-entry control built: `kestrel random-entry`, scorecard test 9 for bracket
  strategies (beat 95% of random-entry paths with the same stops, targets and sizing).
- 2026-11-15 Cost gate built and run on BTC/ETH/SOL 1m and 5m (12 months each ingested):
  FAILS 17 of 18, SOL 5m NY open MARGINAL.
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
