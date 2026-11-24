# Exploration log (not evidence)

The exploration half of the experiment in docs/rules.md. Anything goes here; nothing in this
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

# Round 2: rotation among large coins (2026-11-16, Luke; not evidence)

Script `rotation_round1.py`. Coins: BTC, ETH, SOL (in neither half; heavily searched
already) and the exploration-half large coins BNB, DOGE, LINK, DOT, LTC; confirmation-half
coins never read. 2018-01 to 2025-10, costs on every change. 1,760 settings over universe,
lookback (30-365 days), top 1-3, weekly or monthly, absolute momentum, a BTC moving-average
filter and volatility sizing. "Robust" below: beat both BTC held and an equal-weight hold of
the same coins in both halves (2018-21, 2022-25), drawdown inside 50%.

- The BTC filter does most of the work: 60% of settings with BTC above its 50-day average
  were robust, 5% with no filter (100-day 25%, 200-day 6%). Weekly beat monthly (33% vs
  15%); volatility sizing helped (37% vs 11%). The lookback mattered little.
- The ranking still adds something. Under the same filter (BTC > 50-day, weekly,
  volatility-sized, 90-day lookback), momentum picks beat 95-100% of 100 random picks of
  the same number of coins over 2018-25 in every universe. Over 2022-25 alone (after
  SOL's 2021 run) they still beat BTC held (Sharpe 0.97-1.26 vs 0.69) and beat 68-99% of
  random picks: weakest on BTC/ETH/SOL (68-86%, little to choose between), strongest on
  the 8 large coins (94-99%).
- The original monthly rotation without a filter is the weak version: monthly and
  unfiltered settings were rarely robust, which matches its failed walk-forward and
  holdout.

Leading shape, if anything graduates: each week, while BTC is above its 50-day average,
hold the top 2-3 of a set of large coins by 90-day return, each sized to 50% volatility;
otherwise cash. That is btc-regime-trend's timing with a momentum picker, the altcoin
portfolio the to-do list already names. How graduates would be confirmed is still open (Luke).

## Round 2, part 2: extra filters (`rotation_round2.py`)

The leading shapes: G1, the top 3 of the 8 large coins by 90-day return; G2, the top 1 of
BTC/ETH/SOL/BNB; both weekly, only while BTC is above its 50-day average, each sized to 50%
volatility. Weeks invested broken down by conditions: none lost on average, but weeks with
BTC less than 3% above its average were the weakest (+0.5% a week against +3.0% when more
than 10% above). Sixteen filter combinations each:

- A 3% margin (BTC more than 3% above its average) was the one consistent help: Sharpe
  1.65 to 1.67 (G1) and 1.65 to 1.68 (G2), better in the 2022-25 half on both, drawdown a
  few points lower (-32% to -30%, -41% to -34%), some return given up.
- Stablecoin growth hurt, mostly in 2022-25 (G1 1.20 to 0.86); breadth hurt G1.
- The best of the 16 (margin plus BTC volatility under 80%) was barely better than the
  margin alone: choosing it would be fitting.

Graduated (Luke, 2026-11-16): G1 and G2, each with the 3% margin and nothing else.

## How round 2 ended (2026-11-16)

Confirmed on the unspent holdout year of baskets of large coins neither rule had seen (BTC
plus the largest confirmation-half coins by pre-holdout volume), under Kestrel's holdout
rule: G1 (top 3 of 8) PASSED, flat while BTC fell 29% (-4.7% for BTC at its exposure);
G2 (top 1 of 4) FAILED, -22.6% (-5.7%). The search ranked them as equals (Sharpe 1.55 and
1.51 on the engine); the fresh year separated the diversified one from the concentrated
one. Findings item 15.

# Why G1 works (2026-11-22; exploration, not evidence)

Script `why_g1.py`: G1's rule taken apart on the real portfolio engine (330 USDT, costs,
minimum orders), its own 8 coins, 2018-01 to 2025-10. The switches at G1's settings
reproduce G1's decisions exactly. Sharpe (full, 2018-21, 2022-25):

| Variant | Sharpe | 2018-21 | 2022-25 | Return | Max DD | Exposure |
|---|---|---|---|---|---|---|
| G1 (BTC filter + top 3 by 90-day return) | 1.55 | 2.30 | 0.66 | +4,048% | -37% | 27% |
| Filter alone (every eligible coin) | 1.45 | 2.34 | 0.49 | +2,333% | -39% | 28% |
| Ranking alone (no filter) | 1.30 | 1.88 | 0.70 | +4,655% | -67% | 57% |
| Filter + risk-adjusted top 3 (return / volatility) | 1.53 | 2.34 | 0.50 | +4,045% | -41% | 28% |
| Every coin, no filter (control) | 1.18 | 1.68 | 0.67 | +2,666% | -59% | 58% |
| BTC held | 0.74 | 0.80 | 0.69 | +718% | -81% | 100% |

Filter + 3 random coins (100 seeds): median Sharpe 1.31 (2.11 in 2018-21, 0.42 in 2022-25);
G1 beats 99, 94 and 95 of them.

- **2018-21: the filter is the edge.** Holding every coin while BTC trends does as well as
  G1 (2.34 vs 2.30); the ranking adds nothing there.
- **2022-25: G1 doesn't beat BTC held (0.66 vs 0.69).** Every variant sits at 0.5-0.7.
  The filter alone is worse than BTC (0.49); the ranking pulls it back to level, beating
  random picks (95 of 100) but not holding BTC. The full-period edge is mostly 2018-21's
  broad altcoin boom, on a coin list chosen in hindsight (findings 15).
- **Risk-adjusted ranking doesn't help** (worse in 2022-25: 0.50).
- What this means for paper trading: expect G1 to roughly track BTC's Sharpe with a
  quarter of the exposure and a much smaller drawdown, not to beat it, unless a broad
  altcoin rally comes back. Nothing here changes G1's rule or decision.
