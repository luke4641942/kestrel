# Strategy backlog

Every idea Luke has researched or proposed, and where it stands in Kestrel. Results
recorded before Kestrel (Nov 2024 – Oct 2026) don't count: they came from scripts with
synthetic data, in-sample fitting, lookahead in indicators, sweeps on the test data, or
missing costs. Each idea is rebuilt as a pre-registered research note
(`research/strategies/<slug>/`) and tested once on real Binance data before the holdout.

Status as of 2026-11-11.

## Ready to test (note drafted, code built and tested on synthetic data)

Freeze the note on the Strategies tab, then run exactly what its falsification section
says. Nothing below has been run on market data.

| Idea | Note | Implementation | Test |
|---|---|---|---|
| Trend following with volatility targeting (new) | `trend-following-with-volatility-targeting-btc` | `trend-vol-target` | BTC and ETH **1d**, 2020-01 to 2025-10 |
| S1 Monday range, stage 1: raw-effect study | `monday-range-pullback-aest` | `kestrel anchor-study` (CLI) | BTC and ETH 1h. A KILL rejects the note; stage 2 never runs |
| S1 Monday range, stage 2: pullback to the midpoint | same note | `monday-pullback` | BTC and ETH 1h, only if stage 1 survives |
| S2-C pivot reversal, no regime filter | `pivot-reversal-no-regime-filter-eth-btc-1h` | `pivot-reversal` | ETH and BTC 1h |
| Horizontal level retest | `horizontal-level-retest-resistance-becomes` | `level-retest` vs `level-breakout` | BTC and ETH 4h |
| Rising trendline bounce | `rising-trendline-bounce-in-an-uptrend` (freeze v2) | `trendline-bounce` vs `swing-pullback` | BTC and ETH 4h |
| Falling trendline breakout | `falling-trendline-breakout` | `trendline-breakout` vs `channel-breakout` | BTC and ETH 4h |
| Falling wedge breakout | `falling-wedge-breakout` | `wedge-breakout` vs `trendline-breakout` | BTC and ETH 4h |
| Cross-sectional momentum (S4) | `cross-sectional-momentum-across-coins-top-5` | `xs-momentum` | All coins 1d (the delisted-inclusive universe), against BTC |

Testing this many ideas at once is itself a multiple-testing problem: with seven
hypotheses on two markets, one pass at the 90-95% level is expected by luck alone. Until
P3's deflated Sharpe corrects for every trial, a single pass means "promising", nothing
more.

## Decided

| Idea | Outcome |
|---|---|
| Structure pullback (Craig Percoco setup) | **Rejected 2026-11-05.** 160 trades, +0.057R, P(>0) 63% against 90% required. |
| Backtester sanity gate (backlog "Phase 2 gate") | **Passed.** Buy-and-hold matches the price return exactly; a strategy that looks ahead is refused; random signals average zero before costs and lose their costs after them (`tests/backtest/test_sanity.py`). |

## Waiting, and why

| Idea | Blocked on | Spot-viable? |
|---|---|---|
| S2 HMM regime filter | Luke's call: HMM work comes later. Needs a walk-forward HMM (rolling refit, filtered probabilities, states relabelled by emission means each refit) and a base strategy with an edge to filter. | Yes, as a filter |
| S3 HMM regime playbook | Same, after S2's walk-forward HMM exists. Must beat a plain 200-day moving-average filter after costs. | Yes, long-only version |
| Funding-rate extremes (as a signal) | A new data source: perpetual funding history (published in Binance's bulk archive). Only the signal comes from perps; trades stay spot. | Signal yes, carry no |
| Reversion after liquidation cascades | Liquidation and open-interest history; Binance's public archive covers it only partly. Check coverage before writing a note. | Yes, long after down-cascades |
| Perp/spot basis | Perpetuals, which need the P5 rethink (not offered to Australian retail). | No |
| Cross-exchange lead-lag | Tick or order-book data from several venues and low latency; A$500 is far too small to trade it. | Research only |

## Rules every test follows

- Spot, long only, costs always included (10 bps taker fee, 1 bp half-spread, square-root
  impact), decide at a bar's close and fill at the next open.
- The note is frozen before the first run; every run is logged as a trial; a parameter
  sweep is never part of a first test.
- The backlog's kill criteria map onto Kestrel as follows. "Edge gone at 2x costs": the
  break-even cost must be at least twice the cost paid. "Out-of-sample Sharpe below half
  of in-sample": P3's walk-forward, not yet built.
- The holdout (from 2025-11-01) is used once, for a finished strategy's final verdict.
