# To-do

The active list. Claude keeps it up to date in the same commit as the work (see CLAUDE.md).
Decisions and rules live in CLAUDE.md; results in `research/findings.md`.

## Top: first use of the random-entry control (scorecard test 9)
- [ ] A bracket strategy to put through `kestrel random-entry`. Two steps:
      1. Dry run on channel breakout, BTC 4h 2020-25 (already dropped). It checks the tool
         on real data and adds no trial. It reruns a dropped strategy's code, so Luke's OK
         is needed first.
      2. First real one: G1 below with the 3 x ATR stop. This makes it a bracket strategy,
         with RSI(2) > 50 or day 10 as its exit signals.

## YouTube strategies, batches 1-3 (Luke's specs, 2026-11-15): ranked, nothing built
Ranked by Claude: untried and most promising first. Nothing here is ruled out; dropping
any of them is Luke's call. "Twin" means something close that we already tested and
dropped. Testing a twin, or anything from a rejected family (pullbacks, chart patterns,
reversals), means knowingly overriding the entry bar (Luke), with every run counted as a
trial. Every strategy is spot, long-only, holds at most 3 coins, and is judged by the
scorecard against exposure-matched holding.

Tools first: done 2026-11-15 (see Done recently).

Strategies, most to least promising:
- [ ] 1. READY TO FREEZE: note `turn-of-the-month-on-btc` (strategy `turn-of-month`).
      Turn of the month, BTC 1d (batch 2 A3): hold from the last day's close to the
      first day's close; variant: the last 5 days plus the first 3. Compare with random
      days of the same count. New family (calendar), no twin, cheap: 2 trials. Needs a
      crypto reason in the note (the equity reason is month-end pay and fund flows).
- [ ] 2. Bollinger pullback on a coin basket (batch 3 G1, Rayner Teo). The coin closes
      above its 200-day, closes below the lower band (20, 2.5 SD), and then a limit order
      3% under that close fills. Exit on RSI(2) > 50 or after 10 days. At most 3 coins,
      ranked by 150-day return. It is the dip family (twin: buy big down days, on
      BTC/ETH only), but across alts with a trend filter it is untested. Our universe
      keeps delisted coins, so it avoids the survivorship bias Rayner's test may have.
      Needs engine work: a limit entry that fills at min(limit, open) only if the low
      reaches it. Its 3 x ATR stop variant is the random-entry control's first real
      test (above). Keep the sweep small: 3 widths x 3 depths is already 9 trials.
- [ ] 3. READY TO FREEZE: note `squeeze-breakout-on-btc-and-eth` (strategy `squeeze-breakout`).
      Squeeze breakout (batch 3 G4). Band width at a 120-bar low; buy a close above
      the upper band while above the 200-day; trail on the 20-day MA. It relates to our
      volatile-direction study (TREND), but the long-only version found little.
      Twin: channel breakout (dropped).
- [ ] 4. Order flow, BTC/ETH 1m-5m (batch 2 C, Fabio Valentino): delta, cumulative delta
      and the volume profile from Binance aggTrades. Model 2 first (a failed breakout
      back to the point of control). New family, but 1m stops measured 6-19 bp against a
      22 bp round trip: gate its own stops first. A big data job (trades, not bars).
- [ ] 5. READY TO FREEZE: note `monthly-rotation-across-btc-eth-and-sol` (strategy
      `monthly-rotation`). Momentum rotation, BTC/ETH/SOL/cash monthly (batch 3 G3, with batch 2 A5/A6
      merged in): hold the top 2 by 12-month return that are above their 200-day; sweep
      3/6/12 months. Twins: BTC/ETH rotation (dropped), cross-sectional momentum (dropped).
      Overlaps trend-vol-target (pursue). A5 has no crypto version.
- [ ] 6. READY TO FREEZE: note `rsi-dip-buying-on-btc-and-eth` (strategy `rsi-dip`).
      RSI dip-buying, BTC/ETH 1d (batch 2 A1/A2/A4 and batch 3 G2 merged): RSI(2) < 10,
      RSI(5) < 30 or RSI(10) < 30, above the 200-day. Exit on the close above yesterday's
      high or RSI(10) > 40. Twins: buy big down days, Fear & Greed contrarian, extreme fear
      (all dropped or KILL). G2 has 36 trades in 23 years, so pool coins. At most 8 trials.
- [ ] 7. READY TO FREEZE: note `breakouts-across-a-coin-basket` (strategy `basket-breakout`;
      its ATR stop acts at the close). Basket breakouts (batch 2 B1/B2): a 100-day 3 SD band, or 20% off the 50-day low;
      BTC above its 200-day; lowest volatility first; ATR stop; 3 slots, not 10. Twins:
      channel breakout, 10-coin trend basket (both dropped).
- [ ] 8. READY TO FREEZE: note `cross-sectional-momentum-top-3-over-1-3-months` (existing
      strategy `xs-momentum`, no new code). Cross-sectional momentum, long the top 3 by 1-3 month return (batch 2). Twin:
      top 5 (dropped, -92% drawdown). The video's version is long-short; we only get the
      long half.
- [ ] 9. READY TO FREEZE: note `candle-pattern-and-macd-turn-on-btc-and-eth` (strategy
      `pattern-macd`, a bracket strategy: test 9 applies). Price action + MACD (batch 3 G5): candle pattern at a swing, breakout, MACD
      turn. No stop in the original (its 87% win rate likely hides large losses). Chart
      pattern family; every pattern we tested failed.
- [ ] 10. Set and Forget (batch 1 S1): W/D/4H structure, zones with 3+ touches, 1H
      engulfing entry, 2R-4R. Discretionary zones, pattern family, forex original.
- [ ] 11. CHoCH + FVG and market shift (batch 1 S2/S3, Craig Percoco / Brad Goh): the cost
      gate FAILED on 17 of 18 (0.5-3.9R of costs a trade). Only SOL 5m at the NY open was
      MARGINAL (0.49R). Luke's call: drop, or test SOL 5m NY only. A Percoco structure
      pullback was rejected on 2026-11-05 (+0.06R).
- Not on the list: earnings IV-crush calendars (batch 2 D, US equity options). Running G1
  and G3 50/50 together is a multi-strategy portfolio, which is parked in CLAUDE.md;
  revisit only if both pass on their own.

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
- [ ] Other markets (Luke's idea, 2026-11-15; nothing to work on now). Forex and commodities
      without leaving Binance spot: EURUSDT (euro), PAXGUSDT (gold, one token per ounce),
      and tokenized stocks/ETFs (AAPLBUSDT... from June 2026, too short for now). Wider
      markets give trend strategies more setups (Rayner's point) and test whether an edge
      is crypto-only. Each needs its own spread assumption and its own holding benchmark.
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
- 2026-11-15 Validation additions from batch 2: the max drawdown on 2,000 reshuffled paths
  (median and 1 in 20) on every backtest. Results by year and walk-forward's
  out-of-sample share of in-sample already existed.
- 2026-11-15 Cost gate shown on the dashboard (More > What a trade costs), saved by `kestrel cost-gate`.
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
