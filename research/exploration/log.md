# Exploration log (not evidence)

The exploration half of the experiment in CLAUDE.md. Anything goes here; nothing in this
file is a result, a trial, or evidence. It records how the graduates were found, so the
search can be reproduced and judged. Scripts: `round1.py`, `round2.py`, `round3_engine.py`.

## Round 1 (2026-11-16): 60 daily rules, 181 exploration coins

A fast screen: weights decided at the close, held from the next day, fee plus each coin's
spread band on every change. Score: the share of coins where the rule's Sharpe beats
holding the coin (scorecard test 1), in the whole history and in each half.

- Nothing beat holding on most coins in both halves; the best reached 48%.
- Promising: a BTC regime filter with the coin's own trend, a 10/50 moving-average
  crossover, volatility targeting, and buying after a crash.
- Poor on most coins: weekday effects, strength relative to BTC, volume spikes, RSI(2)
  dips, and long trend lookbacks (120-200 days; Kestrel's 20/60/120 votes rank low on
  altcoins).

## Round 2: grids and combinations, about 170 settings

- BTC above its 50-day average and the coin up over 30 days, sized to 50% volatility:
  beat holding on 83% of coins and in both halves on 65%; median Sharpe 0.60, positive
  on 90%. A plateau: neighbouring filters (50-100 days) and lookbacks (10-30 days) all
  scored within a few points.
- Crash rebound, buy after a 30% fall over 7 days and hold 3 days: 67% and 41%; median
  Sharpe 0.45.
- Volatility targeting alone (30-50%): 65% and 43%; the gap over holding is small.

## Round 3: the real engine on 40 exploration coins (seed 1)

- The BTC-filtered trend: beat holding on 93% of the 40; median return +96% against
  -89% for holding the same coins (most altcoins collapsed).
- Crash rebound: 73%; median +62% while invested about 6% of the time. Checked for
  artefacts: only one of the 40 coins has a gap in its history (no symbol reuse), and the
  profit comes from many separate crashes (2020-03, 2021-04, 2022-05, 2022-11, 2024-04,
  2024-08, 2025-02, 2025-10), the best two months 26% of it. Profits are concentrated
  in a few trades per coin.

Every number above was found by searching, on coins chosen to be searched. Only the
confirmation half can say whether any of it is real.

## How round 1 ended (2026-11-16)

Two ideas graduated (Luke): the BTC-filtered trend (`btc-regime-trend`) and the crash
rebound (`crash-rebound`). Each was frozen as a confirmation note and run once with
`kestrel confirm` on the 180 confirmation coins (pass: beat holding on more than half the
coins and in more than half the pooled walk-forward windows):

| Graduate | Beat holding | Windows won | Verdict |
|---|---|---|---|
| btc-regime-trend | 149 of 180 (83%) | 427 of 771 (55%) | PASS |
| crash-rebound | 111 of 180 (62%) | 274 of 771 (36%) | FAIL |

The search predicted the first well (83% beat holding in round 2, 83% on fresh coins). For
the second it overstated the coin share (67% searched, 62% fresh), and the warning sign it
showed, a weak window test (34% on a 10-coin preview), held on fresh coins (36%). Luke kept
the method. btc-regime-trend was then tested on ETH and SOL and pursued to paper trading
(findings item 13). The confirmation half is spent: a second round needs fresh data and
its own split.
