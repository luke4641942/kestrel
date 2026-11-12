# Strategy backlog

Every idea Luke has researched or proposed, and where it stands in Kestrel. Results
recorded before Kestrel (Nov 2024 – Oct 2026) don't count: they came from scripts with
synthetic data, in-sample fitting, lookahead in indicators, sweeps on the test data, or
missing costs. Each idea is rebuilt as a pre-registered research note
(`research/strategies/<slug>/`) and tested once on real Binance data before the holdout.

Status as of 2026-11-11.

## Decided

Every idea tested so far has been rejected against rules set before its results were seen.

| Idea | Outcome |
|---|---|
| Backtester sanity gate | **Passed.** Exact buy-and-hold, lookahead refused, random signals lose exactly their costs. |
| Structure pullback | Rejected: +0.06R, P(>0) 63%. |
| S1 Monday range | Rejected at the raw-effect study: Monday's range is no different from other days'. |
| S2-C pivot reversal | Rejected: -0.14R / -0.10R; costs ate an edge of about zero. |
| Level retest, trendline bounce, trendline breakout, wedge | Rejected; three of four lost to their plainer comparison. |
| Trend + volatility targeting (2020-25) | Rejected: BTC drawdown -48% vs -38% limit; ETH passed. |
| Trend + volatility targeting (2017-18, unseen) | Rejected: drawdown -35% / -44% vs -83% / -94%, but lost money in the bear market. |
| Cross-sectional momentum | Rejected (v2): Sharpe 0.69 vs BTC 1.07, drawdown -92%. v1 void (faulty spread model). |
| Channel breakout (engine test) | Fails walk-forward: out-of-sample Sharpe 0.41 (BTC), -0.58 (ETH). |

With 31 trials logged, a Sharpe has to beat about 1.50 to stand out from luck (P3's
deflated Sharpe).

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
