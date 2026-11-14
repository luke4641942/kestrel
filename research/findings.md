# Findings

What Kestrel's research has found so far, and how. The decision on each strategy lives in
`research/decisions.toml` (shown on the Overview); each one links to the frozen research notes
in `research/strategies/` that hold the hypothesis, the rules and the pass test written down
before the strategy was run.

## How these were tested

- **Pre-registered.** Each idea was written up (hypothesis, rules, what would falsify it, its
  settings) and frozen before it saw market data. A note is never edited after a run; a change
  needs a new version with a reason, and its runs count as new trials.
- **Costs included.** Binance spot fees, the spread and market impact, on a 330 USDT account
  (about A$500) unless stated.
- **Data before the holdout.** Mostly 2020-01 to 2025-10. Data from 2025-11-01 is held out,
  untouched, for one final verdict on a finished strategy. Some ideas were retested on
  2017-19, which the original tests never saw.
- **Every trial counted.** 172 distinct settings had been tried by 2026-11-14, including every
  setting of the robustness grids. With that many, the best backtest Sharpe you would expect
  from luck alone is about 1.45; the deflated
  Sharpe measures each result against that bar.

The results below were judged against each note's own pass test. From 2026-11-14 every
strategy is judged on one scorecard instead (see CLAUDE.md, "Research rules"); none of the
strategies below would pass it.

## What the evidence says

1. **Trend rules limit losses but haven't beaten holding.** Trend following, a trend basket
   and weekly momentum all cut drawdowns, often by half. But holding less of the coin cuts
   drawdowns too, at no cost, and against the coin held at the same average exposure none of
   them earned more per unit of risk. The best, trend following with volatility targeting on
   BTC, had a Sharpe of 1.21 (holding BTC: 1.07), deflated to about 34-47%.
2. **The trend votes carry it, not the sizing.** The same volatility sizing without the trend
   votes (vol-hold) scored 0.93 on BTC.
3. **Reversal, pullback and chart-pattern rules failed.** Structure pullbacks, pivot reversals,
   buying big down days, a Fear & Greed contrarian rule and four chart patterns: none was
   convincingly better than zero after costs, and each was worse than its simpler control
   (a plain breakout, a plain pullback).
4. **Results on one coin and period rarely repeat.** Weekly momentum passed every test on ETH
   2020-25, then failed on BTC, on ETH 2017-19 and on SOL. Channel breakout was robust to its
   settings but failed walk-forward on both coins.
5. **Baskets of large coins are worse than BTC alone.** And at 330 USDT a basket's slots are
   too small to trade (13.6% of the trend basket's orders were below Binance's minimum).
6. **No lead from BTC at a weekly lag.** BTC's week was no better a signal for ETH or SOL than
   their own.

## Every hypothesis

| Strategy | Market | Result | Decision |
|---|---|---|---|
| Trend following + volatility targeting | BTC 1d | Walk-forward Sharpe 0.97 vs 0.71 holding; robust to settings; deflated 34% | Pursue: paper-traded in P4 as risk-managed holding, not an edge |
| Trend following + volatility targeting | ETH 1d | Walk-forward Sharpe 0.28, a third of in-sample | Drop |
| Trend + vol target, 2017-18 (unseen) | BTC 1d | Cut drawdowns by more than half, lost money in the bear market | (evidence for the above) |
| Channel breakout | BTC, ETH 4h | Sharpe under half of holding; failed walk-forward on both | Drop |
| Structure pullback | BTC, ETH 4h | +0.06R a trade, P(> 0) 63% against 90% needed | Drop |
| Level retest | BTC, ETH 4h | Lost money; worse than buying the breakout | Drop |
| Rising trendline bounce | BTC, ETH 4h | P(> 0) 83% against 90%; worse than without the line | Drop |
| Falling trendline breakout | BTC, ETH 4h | Lost money; a plain breakout made money | Drop |
| Falling wedge breakout | BTC, ETH 4h | +0.17R a trade, P(> 0) 86% against 90% | Drop |
| Pivot reversal (no regime filter) | BTC, ETH 1h | No edge before costs; 0.15R of costs a trade | Drop |
| Monday range pullback | BTC, ETH 1h | The raw-effect study found Monday's range no different; never run | Drop |
| Cross-sectional momentum, top 5 | All coins 1d | -92% drawdown, Sharpe 0.69 vs 1.07 for BTC (v1 void: faulty spread model) | Drop |
| Fear & Greed contrarian | BTC, ETH 1d | +20% vs +1,087% holding BTC; -86% on ETH | Drop |
| Buy big down days | BTC, ETH 1d | +21% vs +42% for BTC at the same exposure; +19% vs +60% on ETH | Drop |
| Funding crowding filter on holding | BTC, ETH 1d | BTC Sharpe 1.07 vs 1.07; ETH 0.87 vs 1.12 | Drop |
| Funding crowding filter on trend | BTC, ETH 1d | BTC 1.27 vs 1.21 for 84% of the return; ETH worse | Drop |
| Trend across a 10-coin basket | Top 10 coins 1d | Drawdown -41% vs -85%, Sharpe 0.95 vs 1.07 for BTC | Drop |
| Volatility-managed holding | BTC, ETH 1d | Below the market at the same exposure on both | Drop |
| Nearness to the 52-week high | Top 20 coins 1d | -64% vs +10% for the same coins held equally | Drop |
| One-week momentum | BTC, ETH, SOL 1d | Passed on ETH 2020-25 only; didn't reproduce on ETH 2017-19 or SOL | Drop |
| BTC/ETH rotation | BTC, ETH 1d | Sharpe 1.06 vs 1.07 for BTC, same drawdown | Drop |
| BTC-led weekly momentum | ETH, SOL 1d | Below holding on every window | Drop |

## Mistakes in the process, and what changed

- **A faulty spread model** made cross-sectional momentum v1's result meaningless. It was
  voided, the spread model was replaced (Abdi-Ranaldo per coin, 1 bp floor) and the strategy
  rerun as v2.
- **One trial was run twice by accident** (trend basket); the results were identical and both
  stay in the trial count.
- **Pass tests drifted between notes** (P(profit), drawdown, start dates). Fixed on 2026-11-14
  with one scorecard for every strategy.
- **A type error was hidden** by piping the check command's output through `tail`. Fixed with
  `scripts/check.sh`, which stops at the first failure.
