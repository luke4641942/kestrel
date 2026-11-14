# Findings

Kestrel has tested about 25 pre-registered trading hypotheses on Binance spot data, at
172 trials in all. None has passed. The closest, trend following with volatility targeting
on BTC, beat holding BTC at the same exposure in every period tested from 2020, but by too
little to rule out luck once every trial is counted (deflated Sharpe 28%, against 95%
required). This document explains how the tests were run, what they found, where the
process went wrong, and what the results can't tell us.

The decision on each strategy is in `research/decisions.toml` (shown on the dashboard's
Overview). Each links to frozen research notes in `research/strategies/` holding the
hypothesis, rules and pass test, written down before the strategy saw market data.

## How the tests were run

- **Pre-registered.** Each idea was written up (hypothesis, rules, what would falsify it,
  its settings) and frozen before it was run. A note is never edited after a run; a change
  needs a new version with a reason, and its runs count as new trials.
- **Costs included.** Binance's 10 bp taker fee, a half-spread (1 bp for BTC and ETH, wider
  for smaller coins) and square-root market impact on every fill.
  Orders above 1% of a bar's volume, or under Binance's 5 USDT minimum, are refused, as
  they would be live. A BTC round trip costs about 22 bp.
- **Realistic size.** A 330 USDT account (about A$500), the size this will trade with.
- **No lookahead.** Decisions use closed bars only and fill at the next bar's open.
  Strategies are checked by recomputing them on truncated histories: a value at bar t must
  not change when later bars are added.
- **A holdout nobody has looked at.** Data from 2025-11-01 was set aside on 2026-10-31,
  before any research, for one final test of a finished strategy. Everything here uses data
  before it, mostly 2020-01 to 2025-10, with some ideas retested on 2017-19.
- **Every trial counted.** 172 distinct settings had been tried by 2026-11-14, including
  every setting of the robustness grids. With that many, the best backtest Sharpe you would
  expect from luck alone is about 1.45. The deflated Sharpe (Bailey and López de Prado)
  measures each result against that bar, allowing for skew and fat tails.
- **A fair benchmark.** Every result is compared with holding the same coin, and with
  holding it at the strategy's average exposure (rebalanced daily, without costs). Being in
  cash half the time halves drawdowns for free, so only beating the second is skill.

Two engines (event-driven and vectorised) are tested against each other and agree to
within 0.13% of final equity. 665 backend and 96 dashboard tests run on every change.

## The closest call: trend following with volatility targeting

Three trend votes, each 1 if today's close is above the close 20, 60 or 120 days ago, set
how much BTC to hold (0, a third, two thirds or all). The position is scaled down when
30-day volatility is above 50% a year. Daily bars, fixed settings, no leverage.

| BTC, 2020-01 to 2025-10, after costs | Strategy | BTC at the same exposure (50%) | BTC held |
|---|---|---|---|
| Total return | +639% | +423% | +1,420% |
| Sharpe | 1.21 | about 1.07 | 1.07 |
| Worst drawdown | -48% | | -77% |

Against the scorecard every strategy is now judged on (CLAUDE.md, "Research rules"):

| Test | Result | |
|---|---|---|
| 1. Beats holding at the same exposure | Sharpe 1.21 vs 1.07; +639% vs +423% | Pass |
| 2. Deflated Sharpe at least 95% | 28% | **Fail** |
| 3. Walk-forward | Sharpe 0.97 out of sample vs 0.71 for holding | Pass |
| 4. At least half of 2-year start windows | Better Sharpe than holding in 58% | Pass |
| 5. Break-even cost at least 2x actual | 288 bp vs 22 bp a round trip | Pass |
| 6. At least 30 trades | 510 | Pass |
| 7. Worst drawdown at most 50% | -48% | Pass, narrowly |
| 8. Neighbouring settings also work | A plateau, not a peak | Pass |

Tests 3 and 4 were measured against holding at full size. Holding at a smaller size has
about the same Sharpe, so the comparison carries over, but they will be rerun against
exposure-matched holding when the scorecard is built into the engine.

It fails the one test that matters most. A Sharpe of 1.21 over six years is what the best
of 172 worthless strategies would often reach. Its record outside 2020-25 is mixed: over
2017-25 it still beat holding (Sharpe 1.07 vs 0.92), but over 2017-18 it cut the worst drawdown
from -83% to -35% yet lost 32% overall, more than holding's 14%. On
ETH it did well in-sample but failed walk-forward (out-of-sample Sharpe 0.28).

So it is paper-traded next (P4), labelled as risk-managed holding and not as a proven edge.
Its purpose there is to prove that live fills match the backtest, not to make money.

## What the evidence says

1. **Trend rules limit losses; only one beat holding, and not by enough.** Trend following,
   a trend basket and weekly momentum all cut drawdowns, often by half. Only trend following
   with volatility targeting also beat the same coin held at the same exposure, and only by
   a margin that 172 trials could produce by chance.
2. **The trend votes carry it, not the sizing.** The same volatility sizing without the trend
   votes (vol-hold) scored a Sharpe of 0.93 on BTC, below holding.
3. **Reversal, pullback and chart-pattern rules failed.** Structure pullbacks, pivot reversals,
   buying big down days, a Fear & Greed contrarian rule and four chart patterns: none was
   convincingly better than zero after costs, and each was worse than its simpler control
   (a plain breakout, a plain pullback).
4. **Results on one coin and period rarely repeat.** Weekly momentum passed every test on ETH
   2020-25, then failed on BTC, on ETH 2017-19 and on SOL. Channel breakout was robust to its
   settings but failed walk-forward on both coins.
5. **Baskets of large coins are worse than BTC alone.** And at 330 USDT a basket's slots are
   too small to trade: 13.6% of the trend basket's orders were below Binance's minimum.
6. **No lead from BTC at a weekly lag.** BTC's week was no better a signal for ETH or SOL than
   their own.

## Every hypothesis

| Strategy | Market | Result | Decision |
|---|---|---|---|
| Trend following + volatility targeting | BTC 1d | Beat exposure-matched holding (Sharpe 1.21 vs 1.07); walk-forward 0.97 vs 0.71; deflated 28% | Pursue to paper trading, as risk-managed holding |
| Trend following + volatility targeting | ETH 1d | Walk-forward Sharpe 0.28, a third of in-sample | Drop |
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
| One-week momentum | ETH 1d | Full scorecard: passes all but luck. Beat exposure-matched holding (Sharpe 1.23 vs 1.12); walk-forward beat holding in 4 of 7 windows; deflated 29%; slightly below holding on unseen ETH 2017-19 (0.13 vs 0.15) | Watch |
| One-week momentum | BTC, SOL 1d | Below holding on both (BTC 0.94 vs 1.07, SOL 1.08 vs 1.24) | Drop |
| BTC/ETH rotation | BTC, ETH 1d | Sharpe 1.06 vs 1.07 for BTC, same drawdown | Drop |
| BTC-led weekly momentum | ETH, SOL 1d | Below holding on every window | Drop |

Each strategy was judged by its own note's pass test, written before it ran. From
2026-11-14 every strategy is judged on one scorecard instead.

## Mistakes in the process, and what changed

- **A faulty spread model** made cross-sectional momentum v1's result meaningless. It was
  voided, the spread model replaced (estimated per coin from daily highs and lows, with a
  1 bp floor) and the strategy rerun as v2.
- **Pass tests drifted between notes**: some used P(profit), some drawdown, some start
  dates. Fixed with one scorecard for every strategy.
- **The goal and the rule disagreed.** The rule was "beat holding"; the strategies that
  looked best cut drawdowns instead. The fix was to compare against holding at the same
  exposure, which credits lower risk fairly without calling it skill.
- **One trial was run twice by accident** (trend basket). The results were identical and
  both stay in the trial count.
- **A type error was hidden** by piping the check command's output through `tail`. Fixed
  with `scripts/check.sh`, which stops at the first failure.
- **A wrong trial count** (72 rather than 172) was quoted during the 2026-11-14 review,
  from reading the study log in the wrong folder. Caught on the dashboard, which reads it
  correctly.

## What these results can't tell you

- **Costs are modelled, not measured.** There is no order-book history, so the spread is an
  estimate and market impact follows a textbook square-root law. Paper trading (P4) will
  measure real fills.
- **One exchange, spot only, long only.** Binance perpetuals aren't available to Australian
  retail clients, so shorting and funding strategies are out of scope.
- **Mostly one market cycle.** 2020-25 contains one full bull and bear market in crypto.
  Results from 2017-19 were sometimes different, as weekly momentum showed.
- **Daily and 4-hour bars.** At 330 USDT, costs rule out anything faster.
- **The holdout is still unused.** No result here has been confirmed on it.
