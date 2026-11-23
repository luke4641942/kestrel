# Findings

Kestrel has tested 88 pre-registered research notes on Binance spot data (866 counted
trials by 2026-11-21, plus an uncounted exploration search). Nothing has passed the full
scorecard: every survivor fails the luck test once every trial is counted. Three strategies
are being paper-traded as the forward test (a one-page summary:
[docs/research-summary.md](../docs/research-summary.md)):

- **Trend following with volatility targeting** on BTC, ETH and SOL. It beat holding each
  coin at the same exposure over 2020-25 and passed the one-off holdout year on all three,
  but won only 2 of 7 out-of-sample windows (2 of 6 on SOL) and deflates to 28-59%.
- **The BTC regime rule** (hold a coin while BTC is above its 50-day average and the coin
  is rising, sized by volatility). It was found by searching one half of the altcoins and
  confirmed once on the other, unsearched half. On ETH and SOL it passes every scorecard
  test but luck. Both coins' holdout years were already spent.
- **G1, a BTC-regime rotation** among 8 large coins (findings 15). It passed a holdout year
  on coins it had never seen, but fails luck and walk-forward on its own, survivorship-
  biased coin list.

This document explains how the tests were run, what they found, where the process went
wrong, and what the results can't tell us.

The decision on each strategy is in `research/decisions.toml` (shown on the dashboard's
Overview). Each links to frozen research notes in `research/strategies/` holding the
hypothesis, rules and pass test, written down before the strategy saw market data.

## How the tests were run

- **Pre-registered.** Each idea was written up (hypothesis, rules, what would falsify it,
  its settings) and frozen before it was run. A note is never edited after a run; a change
  needs a new version with a reason, and its runs count as new trials.
- **Costs included.** Binance's 10 bp taker fee (7.5 bp with the BNB discount from 2026-11-15,
  which needs a BNB balance kept in the account; earlier results used 10), a half-spread (1 bp for BTC and ETH, wider
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
- **Every trial counted.** 805 distinct settings had been tried by 2026-11-16, including
  every setting of the robustness and walk-forward grids. With that many, the best backtest
  Sharpe you would expect from luck alone is about 2.29 (it was 1.45 at 172 trials). The
  deflated Sharpe (Bailey and López de Prado) measures each result against that bar,
  allowing for skew and fat tails.
- **Search, then confirm.** Since 2026-11-16, ideas can also be searched freely on one half
  of the altcoins, uncounted and never evidence, and a few graduates run once on the other,
  locked half under a test fixed in advance, with their own trial count.
- **Costs checked before building.** A minute-chart idea first goes through a cost gate:
  how wide its stops are against a 22 bp round trip, in R, measured without looking at
  outcomes. A bracket strategy must also beat random entries with the same stops, targets
  and sizing, so a 4R-win/1R-loss payoff shape can't pass as an edge.
- **A fair benchmark.** Every result is compared with holding the same coin, and with
  holding it at the strategy's average exposure (rebalanced daily, without costs). Being in
  cash half the time halves drawdowns for free, so only beating the second is skill.

Two engines (event-driven and vectorised) are tested against each other and agree to
within 0.13% of final equity. 1,048 backend and 207 dashboard tests run on every change (2026-11-22).

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
| 3. Walk-forward | Beat holding's Sharpe in only 2 of 7 six-month windows (4 needed); joined, 0.97 vs 0.72 | **Fail** |
| 4. At least half of 2-year start windows | Better Sharpe than holding in 58% | Pass |
| 5. Break-even cost at least 2x actual | 288 bp vs 22 bp a round trip | Pass |
| 6. At least 30 trades | 510 | Pass |
| 7. Worst drawdown at most 50% | -48% | Pass, narrowly |
| 8. Neighbouring settings also work | A plateau, not a peak | Pass |

Holding at a smaller size has the same Sharpe as holding at full size, so tests 3 and 4
compare with holding's Sharpe directly. Test 3 was measured on 2026-11-14 by repeating the
identical walk-forward window by window (note trend-vol-target-btc-scorecard-walk-forward).

It fails two tests: the walk-forward window by window, and the one that matters most. A Sharpe of 1.21 over six years is what the best
of 172 worthless strategies would often reach. Its record outside 2020-25 is mixed: over
2017-25 it still beat holding (Sharpe 1.07 vs 0.92), but over 2017-18 it cut the worst drawdown
from -83% to -35% yet lost 32% overall, more than holding's 14%. On
ETH it did well in-sample but failed walk-forward (out-of-sample Sharpe 0.28).

It then passed the one-off holdout test (below) and goes to paper trading (P4), labelled
risk-managed holding rather than a proven edge.

## The strongest result: the BTC regime rule

Hold an altcoin only while BTC closes above its 50-day average and the coin is above its
close 30 days earlier, sized to 50% annual volatility (capped at 100%); otherwise cash.
Daily bars, no tuning after it was found.

It came from the exploration experiment (CLAUDE.md, research/exploration/log.md): about
230 daily rules searched on 181 altcoins, two graduates, each run once on 180 coins nobody
had looked at. On those it beat holding on 149 of 180 coins and won 427 of 771
walk-forward windows (55%); the other graduate, a crash rebound, failed.

Then, unchanged, on the two large coins Kestrel already trades:

| 2020-25, after costs | ETH | SOL |
|---|---|---|
| Sharpe (holding; trend-vol-target) | 1.33 (1.12; 1.24) | 1.72 (1.24; 1.52) |
| Return (holding at the same exposure) | +1,094% (+398%) | +1,759% (+380%) |
| Worst drawdown | -35% | -32% |
| Walk-forward windows beating holding | 4 of 7 | 4 of 6 |
| Neighbouring settings beating holding | 94% | 94% |
| Deflated Sharpe | 1% | 8% |

It passes every scorecard test but luck on both coins, a higher bar than trend-vol-target
cleared. The luck test counts all 805 trials, though this rule wasn't found on these
coins; its cleaner evidence is the confirmation run. Its weaknesses: it leans on BTC's
trend as trend-vol-target does, beating altcoins that mostly lost 90% is a low bar, and
both coins' holdout years were spent before it existed. Paper trading (brt-eth, brt-sol,
from 2026-11-16) is the test that's left.

## What the evidence says

1. **Trend rules limit losses; two beat holding, neither by enough.** Trend following, a
   trend basket and weekly momentum all cut drawdowns, often by half. Trend following on BTC
   and weekly momentum on ETH also beat the same coin held at the same exposure, but by
   margins that 175 trials could produce by chance, and trend following won only 2 of 7
   six-month windows out of sample.
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
7. **Stops made the survivors worse.** A 2 ATR trailing crash brake on weekly momentum (ETH)
   and trend-vol-target (BTC) lowered both Sharpes (1.23 to 1.21, 1.21 to 0.91) and barely
   moved drawdowns. Most trips sold a dip that recovered; crypto's normal swings are wider
   than a stop that tight, and the strategies' own exits already do the job.
8. **Trend-vol-target already avoids volatile markets.** A filter that stepped out whenever
   the regime model (an HMM) said the market was in its volatile state lowered the Sharpe
   (0.64 vs 0.69): on those days the strategy already held 13% on average, against 54% on
   others, because its trend votes and volatility sizing do the same job.
9. **In volatile spells, MACD's direction continued (a study, not yet a strategy).** On days
   the regime model called volatile, the week after a positive MACD histogram returned
   +1.63% on average and the week after a negative one -2.00% (BTC and ETH, 2021-25, 75
   non-overlapping weeks, P = 98%); on other days the relationship slightly reversed. The
   coins move together, so the sample is smaller than it looks. As a long-only strategy it
   failed (BTC Sharpe -0.13, ETH 0.42 against 0.46 for holding at its exposure, 19-26
   trades): most of the spread was the down weeks, which a long-only account can only sit
   out, and sitting out is what holding less does for free. A short side would change that.
10. **Attention and extreme fear don't pick better weeks.** Weeks after rising Wikipedia
    attention returned +0.65% against +0.74% for the rest (854 weeks, BTC and ETH 2017-25);
    weeks after Extreme Fear returned +0.27% against +0.59% (806 weeks). Liu and Tsyvinski's
    attention effect didn't appear with this stand-in for Google searches, and fear mostly
    followed falls that continued: in crypto, moves tend to carry on, and buying fear or
    dips keeps losing.
11. **After a Binance delisting announcement, coins keep falling until removal (a study).**
    Across 21 announcements (80 coins, 2022-10 to 2025-09), a coin bought at the open the day
    after the announcement and sold at the removal day's open lost 39.8% on average (41.9%
    more than BTC over the same days); 18 of 21 announcements fell, P(mean < 0) 100%. That
    comes after a 31% fall on the announcement day itself and a 16% lag behind BTC over the
    30 days before. There was no rebound to buy: the pre-registered verdict is EXIT RULE (sell
    at once), not REBOUND. For a long-only account this is a safety rule for any strategy
    holding small coins, not an edge; only a short could earn the fall (P5). 30 of 110
    announced coins couldn't be measured, most because their USDT pair had already gone.
    A hypothetical perp short (pre-registered, 2x with liquidations) was INSUFFICIENT: of
    the 80 coins, 50 had no USDT-M perp and 16 had a perp that stopped trading before the
    spot removal, so only 12 coins in 6 announcements could be shorted to the end (10
    announcements needed). Those 12 averaged -7.8% net at 2x (P > 0: 29%): shorts paid
    1.2% in funding, and 3 of 12 were liquidated, including HIFI's +1,357% squeeze during
    its window in 2025-09. The fall is real, but on Binance it was mostly not shortable,
    and where it was, a crowded short got squeezed.
12. **Trend-vol-target hasn't been shown outside crypto.** Run unchanged on gold, EUR and
    GBP (2026-11-16), it lost to holding gold at the same exposure and lost money on both
    currencies. Its edge is shown only on BTC, ETH and SOL, which trended hard in 2020-25;
    time-series momentum's long record in commodities and currencies didn't show up here in
    five years of daily bars.
13. **Searching freely, then confirming on unsearched coins, found one rule that held up
    (the exploration experiment, 2026-11-16).** About 230 daily rules were searched on 181
    exploration coins (not trials, not evidence); two graduated, and each was run once on
    the 180 confirmation coins nobody had looked at. Holding an altcoin only while BTC is
    above its 50-day average and the coin is up over 30 days, sized to 50% volatility,
    beat holding on 149 of 180 coins (83%) and won 427 of 771 walk-forward windows (55%):
    PASS. Buying a 30% weekly crash for 3 days beat holding on 62% of coins but won only
    36% of windows: FAIL. Caveats: most altcoins lost about 90% in 2020-25, so beating
    them on Sharpe is a low bar, and 55% of windows is a narrow margin; the rule leans on
    BTC's trend, as trend-vol-target does. A pass is not a pursue: it still needs its own
    scorecard, a rule for which coins to hold, and a holdout test.
    Run unchanged on ETH and SOL (2026-11-16; heavily searched coins, so not a
    confirmation), it beat holding at the same exposure and trend-vol-target on both
    (Sharpe 1.33 and 1.72) with drawdowns near -33%, and passed walk-forward and
    robustness; it fails only luck against the main bar, and both coins' holdout years
    are already spent. Pursued to paper trading (Claude's call, delegated by Luke).
14. **Neither funding nor taker flow picks next week's winners among liquid coins
    (2026-11-16, after an outside review proposed market-structure signals).** On the
    exploration half plus BTC, ETH and SOL, 234 Sunday closes (2020-09 to 2025-10, a median
    of 19 liquid coins a week), with past week and size as controls: buyer-led taker flow
    had a slope of +0.17% a week per SD (P 86%, 90% needed) and its top fifth beat the
    average coin by 0.52% a week, but as money that top fifth lost 38% after costs
    (drawdown -92%) because the average liquid altcoin lost 57%; most of its edge was the
    past week's rise. Low funding (the uncrowded side) did worse, not better: slope -0.26%
    a week per SD (P(> 0) 9%), and its top fifth lost 87% after costs; the crowded side
    lost 53%. Long-only, both are only coin pickers, and only inside a rule that already
    decides when to hold altcoins at all; the BTC regime rule is the candidate.
15. **A BTC-regime momentum rotation passed a holdout year on coins it had never seen
    (2026-11-16).** Exploration round 2 searched 1,760 rotation settings on BTC, ETH, SOL
    and large exploration-half coins (not evidence): the BTC 50-day filter did most of the
    work, and under it 90-day momentum picks beat random picks. Two graduates, each weekly
    while BTC closes 3% above its 50-day average and volatility-sized, were run once on
    the unspent holdout year of BTC plus the largest confirmation-half coins, chosen by
    volume alone. Holding the top 3 of 8 finished flat while BTC fell 29% (BTC at its 18%
    exposure: -4.7%), worst drawdown -11%: PASS. Holding only the top 1 of 4 lost 22.6%
    (BTC at its exposure -5.7%): FAIL; one coin was too concentrated. One falling year and
    about 50 weekly decisions: support for forward paper trading, not proof.
    G1's full scorecard on its own coins (2018-25) fails only luck and walk-forward (5 of
    11 windows, its out-of-sample Sharpe equal to in-sample): trend-vol-target's standard,
    with a pass on unseen coins added.
    **Survivorship (disclosed 2026-11-16, after an outside review):** G1's 8 coins are
    today's large coins, picked in 2026. Its 2018-25 backtest never holds a coin before
    it lists, but it only contains coins that went on to succeed (SOL and DOT didn't exist
    in 2018; large coins of 2018 that faded aren't in it), so those numbers flatter it. The
    holdout year is clean of this: that basket was fixed from volume before 2025-11.

16. **The pursued strategies are mostly one bet: long crypto while it trends up
    (2026-11-16, `kestrel overlap`).** Daily returns of the six pursued runs (trend-vol-target
    on BTC, ETH, SOL; btc-regime-trend on ETH, SOL; G1), 2020-08-13 to 2025-10-31 (1,907
    days), read from saved runs (no trials). Correlation follows the coin, not the
    strategy: trend-vol-target and btc-regime-trend correlate 0.82 on ETH and 0.83 on SOL,
    0.35-0.69 across coins; G1 0.42-0.58 with each. BTC explains 64% of trend-vol-target
    BTC's daily variance, 39% on ETH, 8-23% for the rest (betas 0.18-0.45). By BTC's
    month, all six earn only in rising months (+0.30 to +0.40% a day) and lose in falling
    ones (-0.06 to -0.22% a day), except G1 (+0.01%); flat months are flat. On BTC's 206
    worst days (down over 3%) their link to BTC weakens (mostly in cash), but G1 and
    btc-regime-trend ETH move together more (0.73). An equal mix has a Sharpe of 1.79
    against 1.73 for the best alone; it rises without trend-vol-target ETH (1.87) or BTC
    (1.81). In-sample Sharpes of selected strategies, so optimistic; this decides nothing,
    but a second edge has to come from somewhere other than trend.

17. **Shorting the trend doesn't pay; collecting funding does (hypothetical, 2026-11-16,
    `kestrel short-study`).** Five frozen notes, each run once on 2020-01 to 2025-10
    (funding history starts 2020), costed as a 2:1 CFD (2x the spread band, 5% a year
    financing) with a Binance perp reported. Adding a short side to every pursued trend
    rule made it worse: btc-regime-trend's mirror short on ETH and SOL (Sharpe 1.30 and 1.35
    against 1.47 and 1.64 long-only; short legs +13% and +6%, P 72% and 68%), G1 shorting
    its 3 weakest coins (1.13 against 1.66; short leg -49%) and symmetric trend-vol-target
    (0.82-0.97 against 1.23-1.45; short legs -35% to -46%). Each beat its long-only rule only
    in 2022, the one long bear year, and lost to it in the others: crypto's rebounds take
    back what the shorts make. Long/short momentum among liquid coins (top 3 against bottom
    3, weekly, near market-neutral: beta -0.05) made +39% in six years (Sharpe 0.44, P 84%,
    95% needed), all from the long leg (+119%; shorts -50%): FAIL. **Cash-and-carry passed
    every test**: holding BTC, ETH and SOL against their perps while the last week's funding
    was positive earned +6.6% a year on equity, positive in 5 of 6 years (2022 -5.9%, when
    funding turned negative), drawdown -6.6%, 53 trades, 14 margin calls. It is a
    collection of leveraged longs' funding, not a price bet. Caveats: the spot-perp basis
    isn't modelled (perp prices aren't stored), so the drawdown is a floor; Schmeling,
    Schrimpf and Todorov (BIS 2023) find high carry predicts crashes; at 330 USDT each hedge
    is about 73 USDT, which may be under a perp's minimum order (unchecked); and it needs a
    real perp, wholesale-only in Australia today. 8 trials (846 in all; luck bar 2.30).

18. **A coin's first perp listing marks a local top (a study, 2026-11-18, `kestrel
    event-study perp-listing`).** 102 coins of the exploration half (plus BTC, ETH, SOL)
    whose Binance USDT-M perp first settled funding from 2020-01 on, with 60+ days of spot
    before it. In the 30 days before the listing they rose +100% on average against the
    other coins; in the 30 days after (from day 1's open) they lagged the other coins by
    -15.2% on average (P(< 0) 100%; 75% of events below zero), every year from -2.7% (2022,
    6 events) to -23.4% (2021), and worse than all 200 placebo draws of the same coins on
    random days (placebo median -1.3%): EXIT RULE by the note. Robustness, deciding
    nothing: against the median other coin -5.5% (median event -9.8%, 68% below zero),
    against BTC -7.9% (median -17.5%); the coin's own return averaged +1.5% (median -8.3%),
    so the rule is about relative weakness, not a guaranteed fall. For an altcoin
    portfolio: don't buy a coin, or hold it, through its first month of perps. Who pays,
    as the note argued: the perp lets pessimists in after a run (Hale et al. 2018 on
    Bitcoin's CME launch). The first run was void (findings, Mistakes).
    As a trade it fails (note shorting-new-perp-listings-hedged-with-btc, hypothetical,
    2026-11-18): shorting each new perp for 30 days against an equal BTC long, 3 at a time,
    made +11% in six years (Sharpe 0.25), mean trade +2.3% on its slot (P 80%, 95% needed),
    positive in 3 of 6 years, drawdown -56%. Three things ate the edge: 15 squeezes closed
    shorts out at +50% in a day, new perps' negative funding cost 18% of equity (crowded
    shorts pay), and 29 of 102 events found the book full. Unhedged, the short lost 68%.
    DROP: the effect is real as a reason not to hold a coin, not as a short.

19. **Large token unlocks don't show a drift we can use (a study, 2026-11-21, `kestrel
    event-study token-unlocks`).** DefiLlama's public unlock schedules (375 protocols),
    mapped to Binance coins by a strict ticker rule (any ticker shared by two CoinGecko
    ids dropped), left 122 unlocks of at least 2% of the unlocked supply on 12
    exploration-half coins, 2020-25. In the 30 days before the unlock they did +0.4%
    against the other coins (P(< 0) 47%; 63% of events below zero): KILL. The same coins on
    random days did worse (placebo median -4.5%): these coins lag in general, and the
    unlocks add nothing. Deciding nothing: -3.1% in the last two weeks; insider unlocks
    (team, investors) -7.1% but only 7 events; unlocks large for the coin's daily volume
    -2.1% against +2.6% for small ones. The published "90% of unlocks fall" (Keyrock) is
    mostly about coins that fall anyway. Caveats: 12 coins (the ticker rule was strict);
    schedules as documented today.

20. **An altcoin portfolio on the BTC regime, with the exit rules, is worse than holding a
    little BTC (2026-11-21, note altcoin-portfolio-on-the-btc-regime-with-exit).** G1's
    weekly rule on a point-in-time universe (exploration-half coins with 20M USDT a day
    and a year of history, a median of 14 a week), top 3 by 90-day return while BTC is 3%
    above its 50-day average, selling on a delisting announcement or a new perp.
    2019-01 to 2025-10 (2019 in cash by construction: a year of history first): +101%,
    Sharpe 0.53, drawdown -33%, 17% exposure, 433 trades, costs 7.8x covered. The note's
    edge test, against the large-basket control on the same coins (+39%, Sharpe 0.44,
    drawdown -85%), passes narrowly, and all six neighbours (top 2/4, 60/120 days, 2%/4%)
    beat it too (0.46-0.70). But BTC held at the same 17% exposure made +113% (Sharpe
    1.10); walk-forward beat holding in 3 of 9 windows; 2-year starts 32%; luck 0%. Not
    every test but luck, so no holdout test by the note. Picking altcoins under the BTC
    regime adds turnover and alt risk for less than a small BTC position gives. 16 trials
    (863 in all; luck bar 2.28).

21. **Tori Trades' trendline method doesn't beat holding at the same exposure (2026-11-21,
    note tori-trades-trendline-method-long-only; Luke's request, a rejected family,
    disclosed).** Long only, mechanical: buy a close above a falling line through swing
    highs touched 3+ times; stop at the low the break started from; no target, exit on a
    close below the new rising line. BTC, ETH, SOL 4h, 2020-25: +26%, +45%, +65% (Sharpe
    0.41, 0.57, 0.82), drawdowns -18% to -25%, 212-247 trades, +0.17R to +0.40R a trade
    after costs; but in the market only 6-10% of the time, and the coin held at that same
    exposure did better on Sharpe every time (1.07, 1.12, 1.24). Fails test 1 on all three:
    drop by the note. Her exit (no target, a trailing line) did better than our earlier
    trendline tests' fixed 3R targets (those lost money), but not enough to beat simply
    holding a little of the coin. Grids skipped for a strategy failing test 1 (Luke's
    standing rule). 3 trials.

22. **Three non-trend signals don't pick better weeks once past weeks are controlled for
    (2026-11-22, `kestrel signal-study`; notes does-us-buying-on-coinbase-lead-prices,
    does-a-high-variance-premium-lead-prices, does-dollar-liquidity-lead-crypto).** Chosen to
    be independent of the trend bet (findings 16), each pre-registered with a control: the
    spread must be positive both after a rising and after a falling past week. BTC and ETH
    pooled, Sunday closes, one run each:
    - US buying on Coinbase (its 7-day premium over Binance, USDT-adjusted, above its 180-day
      median), 2021-11 to 2025-10: spread +0.67% a week, P 79% (90% needed); +1.42% after a
      falling week, -0.35% after a rising one: KILL. A rebound effect, not a demand signal.
    - A high variance premium (Deribit DVOL less 30-day realised volatility, the same rule),
      2021-09 to 2025-10: +0.73%, P 81%; +1.45% after falls, -0.20% after rises: KILL, the
      dip-buying family in disguise, as the note warned.
    - Dollar net liquidity growing over 4 weeks (Fed assets less the Treasury account and
      reverse repo), 2018-01 to 2025-10: -0.71%, P 17%, negative after rises and falls alike:
      KILL; weeks after liquidity grew were worse. Matches the New York Fed's finding that
      Bitcoin doesn't respond to rate surprises.
    Deciding nothing, by its note: the Coinbase premium from 2018 with USDT taken as a dollar
    passed (spread +1.32%, P 96%, positive after rises and falls). It can't count: it was
    labelled beside the result before the run, and its extra years (2018 to early 2021) are
    when USDT itself swung, so there the "premium" is partly fear of USDT. Pursuing it would
    need a new note, disclosed as chosen after a look, judged forward only.

23. **The regime preset is already the right model, and faster regimes warn early but mostly
    falsely (2026-11-22, research/regimes/; descriptions, not trials).** Variants of the HMM
    were compared by out-of-sample likelihood, walking forward month by month on BTC and ETH
    before the holdout, each scored on the same days and the same shared inputs. Scored on
    return and volatility together, 5 states beat the preset's 3 by 0.26-0.37 nats a day;
    volume, distance from the 1-year high and (for ETH) BTC's inputs all made it worse; a
    shorter window helped BTC and hurt ETH. Scored on returns alone, no variant beat the
    preset by more than 0.002 nats a day and on ETH all were worse: the extra states only
    track slow-moving 20-day volatility more finely. The preset (3 states, 4 years) stays.
    Faster models (the same shape on 4h and 1h bars, 20-bar volatility, a 1-year window),
    2021-01 to 2025-10: the daily model had 11-12 volatile spells of about three weeks. A
    4h model was volatile 27% of the time in about 115 spells of 3.5 days; it was volatile in
    the week before 82-100% of daily onsets (about 50% would be chance at that rate),
    starting a median 40-44 hours earlier, but 72-80% of its spells were never confirmed by a
    daily close within a week. The 1h model flickers (about 430 spells, 20 hours each;
    calling 100% of onsets is near chance, about 85%). A real early warning at 4h, three times
    in four a false one; trading on it would need its own note.
24. **Stepping out on the 4h model's warnings made trend-vol-target worse on every coin
    (2026-11-23, note `trend-vol-target-out-in-the-4h-volatile-regime`; 6 trials, 872 in all,
    luck bar 2.27).** The daily filter (finding 8) failed because it came too late; the 4h
    model warns ~40 hours earlier, so it was given one try, pre-registered to pass on 2 of 3
    coins. Filter on vs off, same days: BTC Sharpe 0.41 vs 0.64 (2020-12 to 2025-10), ETH
    0.63 vs 0.72, SOL 0.12 vs 0.78 (2021-08 to 2025-10); returns +43% vs +104%, +100% vs
    +152%, -2% vs +138%. Drawdown was barely shallower on BTC (-46.8% vs -48.0%) and SOL (-35.6% vs -36.4%),
    deeper on ETH (-42% vs -38%). It traded no more often but turned over 1.3-1.7x as much
    (it sells whole positions); costs rose 26% on BTC and 43% on ETH, and on SOL the
    break-even cost fell below the actual one (18 vs 22 bps). The false alarms sell good days: being out
    when the 4h model is volatile misses rebounds as well as falls. Regime timing does not
    help this rule at either speed; its own trend votes and volatility sizing already do
    the job.

25. **Forex trend lost money after costs, and the costs are structural (2026-11-23, note
    `forex-trend-across-the-10-pairs`, the first forex trial; forex pool 1 trial, 875 across
    all pools).** Textbook 12-month time-series momentum (Moskowitz, Ooi and Pedersen 2012)
    across the 10 pairs, each sized to 3% volatility, capped at 3x gross, 2006-02 to 2022-12,
    500 AUD: -30.2% (CAGR -2.1%), Sharpe -0.07 (95% CI -0.55 to 0.40), max drawdown -58%,
    underwater for 14 of the 17 years, Sharpe above zero in 2 of 8 two-year windows. It
    fails every test its note set. Before costs it made about +2.3% a year (Sharpe 0.16,
    checked by hand against the engine), in line with reports that currency momentum faded
    after 2008. Costs took more than that: spreads 106 AUD and financing -176 AUD, chiefly
    the broker's markup, about 1% a year on a book averaging 2.8x gross, so roughly 2.8% of
    equity a year before any carry. The general lesson for forex at retail CFD terms: a
    strategy must earn more than its gross exposure times the markup, every year, before it
    earns anything; leverage multiplies that drag, not just the returns. Turnover was 46x
    equity a year (daily volatility rescaling through a 2% band per pair).

26. **Forex carry collected its interest and lost far more to the 2008 crash (2026-11-23,
    note `forex-carry-across-the-10-pairs`; forex pool 2 trials, luck bar 0.11; 876 across
    all pools).** Hold each pair towards its higher yield where the published rate
    difference beats the 1% markup, 3% volatility per pair, monthly, 2006-02 to 2022-12, 500
    AUD: -50.2% (CAGR -4.0%), Sharpe -0.37 (P(true Sharpe > 0) 6%), max drawdown -60%,
    skew -1.0 and kurtosis 30, 2 of 8 two-year windows. It fails every test its note set.
    The mechanism worked: financing earned +103 AUD and spreads cost 5. Prices took ~350:
    +5% in 2006, -13% in 2007 and -47% in 2008 as carry trades unwound together, then
    near-zero rates left a small book (0.4-1x gross) drifting a few percent either way to
    2022. That is the published crash risk of carry (Brunnermeier, Nagel and Pedersen
    2008), taken at its worst point and never earned back at retail terms. Its daily
    returns correlated -0.11 with the rejected trend run: a different bet, equally unpaid.
    Two textbook forex premia, run once each at retail CFD costs, both lost from 2006.



## The holdout year (2025-11 to 2026-10)

Reserved on 2026-10-31, before any research, and read once on 2026-11-14 for the two watched
strategies, under rules frozen in advance (notes `holdout-weekly-momentum-eth`,
`holdout-trend-vol-target-btc`). Both coins fell, so each strategy's return was compared with
holding the same coin at the strategy's average exposure.

| Strategy | Return | Holding at same exposure | Worst drawdown | Holding's drawdown | Verdict |
|---|---|---|---|---|---|
| Trend-vol-target, BTC | -2.4% | -8.9% (33%) | -11% | -47% | Pass |
| Weekly momentum, ETH | -25.1% | -16.7% (43%) | -35% | -60% | Fail |
| Monthly rotation BTC/ETH/SOL, 180 days (read 2026-11-15) | -31.5% | -6.6% for BTC (25%) | -32% | -47% | Fail |
| Monthly rotation with a MACD filter (read 2026-11-15; one-sided) | -25.2% | -5.4% for BTC (21%) | -31% | -47% | Fail |
| Trend-vol-target, ETH (read 2026-11-15) | -7.1% | -12.2% (32%) | -16% | -60% | Pass |
| Trend-vol-target, SOL (read 2026-11-15) | +2.6% | -13.8% (28%) | -19% | -67% | Pass |
| BTC-regime rotation G1, top 3 of BTC + 7 unseen large coins (read 2026-11-16) | +0.0% | -4.7% for BTC (18%) | -11% | -47% | Pass |
| BTC-regime rotation G2, top 1 of BTC + 3 unseen large coins (read 2026-11-16) | -22.6% | -5.7% for BTC (22%) | -23% | -47% | Fail |
| Stablecoin hold sized by volatility, BTC (read 2026-11-22; watch, rejected note) | -7.7% | -14.7% (53%) | -20% | -47% | Pass |
| Stablecoin hold sized by volatility, ETH (read 2026-11-22; watch, rejected note) | -28.2% | -18.1% (46%) | -29% | -60% | Fail |

On Sharpe the verdicts are the same (trend -0.06 vs -0.57 for holding; weekly -0.74 vs -0.57;
rotation -1.21 vs -0.57 for BTC). Rotation lost more than BTC held in full while only a
quarter invested: its months in the market were in ETH and SOL, which fell further.
The stablecoin pair (notes `holdout-stablecoin-vol-hold-btc`, `-eth`, Luke's request) was read
after its scorecard had failed, as evidence for its watch decision only, with the year already
known to have fallen; the note fixed a split as a split. It is paper-traded from 2026-11-21
(scs-btc, scs-eth). DefiLlama's stablecoin history is revised after the fact: a week after
download, 1,233 of its days differed (median 0.14%, largest 1.7%) and the 4-week growth flag
flipped on 34 of 3,253 days, so every stablecoin backtest leans on one reconstruction. The
paper runner appends new days and never rewrites saved ones.
One falling year is a small sample: the pass is support for paper trading, not proof of an
edge, and the holdout can't be used again for either strategy.

## Every hypothesis

| Strategy | Market | Result | Decision |
|---|---|---|---|
| Trend following + volatility targeting | BTC 1d | Beat exposure-matched holding (Sharpe 1.21 vs 1.07); beat holding in only 2 of 7 walk-forward windows; deflated 28%; passed the holdout | Pursue (paper trading) |
| Trend following + volatility targeting (first test, 2026-11-12) | ETH 1d | Walk-forward Sharpe 0.28, a third of in-sample, under an older rule | Superseded: finished 2026-11-15 (below), pursue |
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
| One-week momentum | ETH 1d | Full scorecard: passes all but luck. Beat exposure-matched holding (Sharpe 1.23 vs 1.12); walk-forward beat holding in 4 of 7 windows; deflated 29%; slightly below holding on unseen ETH 2017-19 (0.13 vs 0.15); failed the holdout | Drop |
| One-week momentum | BTC, SOL 1d | Below holding on both (BTC 0.94 vs 1.07, SOL 1.08 vs 1.24) | Drop |
| BTC/ETH rotation | BTC, ETH 1d | Sharpe 1.06 vs 1.07 for BTC, same drawdown | Drop |
| BTC-led weekly momentum | ETH, SOL 1d | Below holding on every window | Drop |
| Crash brake on weekly momentum | ETH 1d | Sharpe 1.21 vs 1.23 without it; drawdown -41.4% vs -41.6%; 16 of 29 trips sold a dip that recovered | Drop |
| Crash brake on trend-vol-target | BTC 1d | Sharpe 0.91 vs 1.21; return +272% vs +639%; drawdown -45.9% vs -48.3% | Drop |
| Regime filter on trend-vol-target | BTC 1d | Sharpe 0.64 vs 0.69 (2021-05 to 2025-10); the plain rule already holds 13% in volatile spells | Drop |
| 4h regime filter on trend-vol-target (a second try at the row above, disclosed) | BTC, ETH, SOL 1d, 2020-12/2021-08 to 2025-10 | Sharpe 0.41/0.63/0.12 vs 0.64/0.72/0.78 for the plain rule; fails its adoption test on all three | Rejected (Luke) |
| Forex trend, 12-month time-series momentum (textbook; first forex trial) | 10 forex pairs 1d, 2006-02 to 2022-12 | -30%, Sharpe -0.07, drawdown -58%, 2 of 8 two-year windows; +2.3%/yr before costs, eaten by spread and a ~2.8%/yr financing markup at 2.8x gross | Rejected (Luke) |
| Forex carry, towards the higher yield where it beats the 1% markup (second forex trial) | 10 forex pairs 1d, 2006-02 to 2022-12 | -50%, Sharpe -0.37, drawdown -60%; earned +103 AUD financing, lost ~350 on prices, mostly 2007-08 (-13%, -47%) | Fails its rule; rejection waits for Luke |
| Study: MACD direction in volatile spells | BTC, ETH 1d | Spread +3.70% a week (up +1.63%, down -2.00%), P = 98%: TREND | Study passed |
| Study: rising attention (Wikipedia) | BTC, ETH 1d | Next week +0.65% after rising attention vs +0.74%; P 46%: KILL | Drop |
| Study: extreme fear | BTC, ETH 1d | Next week +0.27% after Extreme Fear vs +0.59%; P 39%: KILL | Drop |
| Ride volatile spells in MACD's direction | BTC, ETH 1d | Weekly hold: BTC Sharpe -0.13 vs 0.53 holding; ETH 0.42 vs 0.46; 0 and 1 of 5 walk-forward windows; 26 and 19 trades; robustness no edge / fragile. Daily switch no better | Drop |
| Monthly rotation with a MACD filter (Luke's idea, after the holdout) | BTC/ETH/SOL 1d 2018-25 | Sharpe 1.41 vs 0.74 for BTC; walk-forward 6 of 11 windows, out-of-sample 1.77; robust (every neighbour 1.26-1.49); passes 7 of 9 tests, fails drawdown (-50.7%, limit 50%) and luck (deflated 57%). Designed after seeing where the plain rotation failed, so hindsight can't be ruled out; failed its one-sided holdout (-25.2% vs -5.4% for BTC at its 21% exposure) | Drop |
| BTC and gold rotation | BTC/PAXG 1d 2020-08 to 2025-10 | Sharpe 0.54 vs 1.01 for BTC; 19 trades; walk-forward 3 of 6 | Drop |
| ETH/BTC relative value (hold the cheap one, else half each) | BTC/ETH 1d 2018-25 | Sharpe 0.74 vs 0.70 for half each (its added test, barely) and 0.74 for BTC; drawdown -87.5%; walk-forward 5 of 11; paid 140 USDT in costs against 7 for half each; deflated 5% | Drop |
| Cost re-check at BNB fees (7.5 bps a side) | Trend-vol-target BTC; rotation | Verdicts unchanged: trend Sharpe 1.21 -> 1.23 (deflated 38%, walk-forward 2 of 7); rotation plain 1.25, MACD 1.41. Costs were never what held them back | - |
| Study: stablecoin supply growth | BTC, ETH 1d 2018-25 | Next week +0.78% after 4-week supply growth vs -0.38% after shrinkage; spread +1.17%, P 91% (bar 90%): PROCEED. Little overlap with momentum (past week rose 54% vs 45-50%). Caveats: P only just clears the bar; pooled BTC and ETH weeks are not independent; DefiLlama's history is a reconstruction | Study passed |
| Hold while stablecoin supply grows | BTC, ETH 1d 2018-25 | Beat holding at the same exposure on both coins (Sharpe 0.81 vs 0.74 BTC, 0.75 vs 0.68 ETH; in the market 74% of the time) but fails five tests: walk-forward 4 and 5 of 11, start dates, 27 trades (30 needed), drawdown -66% and -80%, deflated 4-6% | Watch |
| Monthly rotation with a stablecoin filter | BTC/ETH/SOL 1d 2018-25 | Sharpe 1.19 (below the plain rotation's 1.25) vs 0.74 for BTC; drawdown -59%; walk-forward 3 of 11; deflated 32% | Drop |
| Study: volatile spells split by stablecoin growth | BTC, ETH 1d | In volatile spells the weeks after growth averaged -0.75% vs +0.71% after shrinkage (53 and 22 weeks; P 21%): KILL. The opposite sign to the idea, though not significant; on other days growth still led (+0.82% spread, P 80%) | Drop |
| Trend-vol-target with a stablecoin vote | BTC 1d 2020-25 | Sharpe 1.22 vs 1.23 for the plain strategy (its added test) and 1.07 for BTC; walk-forward 2 of 7; deflated 37%. The vote adds nothing | Drop |
| Stablecoin hold sized by volatility | BTC, ETH 1d 2018-25 | Better than the plain hold: Sharpe 0.87 vs 0.74 (BTC) and 0.87 vs 0.68 (ETH); drawdowns -57% and -51% (limit 50%); walk-forward 5 and 6 of 11; deflated 9% and 8% | Watch (with the stablecoin hold) |
| Trend-vol-target, finished on ETH and SOL (2026-11-15) | ETH 1d 2020-25; SOL 1d 2020-08 to 2025-10 | Both match BTC's standard (fail only luck and out of sample). ETH: Sharpe 1.24 vs 1.12, +896% vs +573% at the same exposure, drawdown -38%, deflated 33%, walk-forward 2 of 7. SOL: Sharpe 1.52 vs 1.24, +1,211% vs +553%, drawdown -42%, deflated 59% (the best in Kestrel), walk-forward 2 of 6. The same rule beats holding on all three major coins | Both passed their holdouts: pursue (Luke) |
| Trend-vol-target on BTC, ETH and SOL together (a third each) | 3 coins 1d 2020-25, 990 USDT | Sharpe 1.59 vs 1.31 for holding a third of each (its added test: pass) and 1.07 for BTC; +1,021%; drawdown -42% vs -85% for the held thirds; in the market 39% of the time; break-even 376 bps vs 18; robust. Fails luck (deflated 59%) and out of sample (2 of 7 windows), like each coin alone. The highest Sharpe in Kestrel | Watch |
| Cost gate: 1m/5m change of character + fair value gap (YouTube S2/S3) | BTC, ETH, SOL 1m and 5m | Stops only, no outcomes (not a trial). Median stop 6-19 bp on 1m and 15-45 bp on 5m, so a 22 bp round trip costs 0.5-3.9R a trade; break-even win rate at 4R 30-97% instead of 20%. FAILS on 17 of 18 (coin x bar size x all hours / NY open); SOL 5m at the NY open MARGINAL (0.49R) | Not built (Luke's call) |
| Cost gate: 1m/5m order flow Model 2, failed breakdown back to the point of control (YouTube batch 2 C) | BTC, ETH, SOL; 5m 2020-25, 1m 2024-11 to 2025-10 | Stops only (not a trial); delta from each bar's taker-buy volume. 1m FAILS on 5 of 6 (median 0.45-1.30R of a 17 bp round trip). 5m (680-884 setups a coin at all hours): BTC FAILS (0.53R), ETH and SOL MARGINAL (0.36R, 0.26R), SOL at the New York open CARRIES (0.18R, 45 setups). The setup's own target (the point of control) sits a median 0.9-1.7R away, so even the carrying cases need about 50-62% wins to break even | Tested anyway (Luke); failed, below |
| YouTube batches 2-3, run 2026-11-15 (eight frozen notes, 22 settings, 280 trials with robustness) | | Every one FAILS the scorecard; all fail luck (deflated 0-43%) | Notes rejected (Luke) |
| Turn of the month | BTC 1d 2018-25 | Sharpe 0.37 (last 0/first 1) and 0.68 (5/3) vs 0.74 holding; walk-forward 5 and 4 of 11 | Drop |
| Bollinger pullback, coin basket | Coins 1d 2020-25 | Sharpe 0.54 / 0.82 / 0.32 (3% limit / 7% / BTC filter) vs 1.07 for BTC; walk-forward beat BTC in 2, 1 and 0 of 7 windows | Drop |
| Squeeze breakout | BTC, ETH 1d 2018-25 | Sharpe 0.61 vs 0.74 and 0.18 vs 0.68; 22 and 16 trades; walk-forward 0 of 11 on both | Drop |
| Monthly rotation BTC/ETH/SOL | 1d 2018-25 | Sharpe 1.13 / 1.25 / 1.26 (365/180/90 days) vs 0.74 for BTC, but drawdown -59% (limit 50%) and deflated 30-43%; walk-forward beat BTC in 3 of 11 windows on every lookback (the gains came from holding ETH and SOL in 2021) | Drop |
| RSI dip-buying | BTC, ETH 1d 2018-25 | Six settings, Sharpe 0.04-0.52 vs 0.74 (BTC) and 0.68 (ETH); walk-forward 0-3 of 11 | Drop |
| Basket breakouts | Coins 1d 2020-25 | Sharpe 0.85 (Bollinger) and 0.89 (20% off the low) vs 1.07 for BTC; drawdowns -77% and -62%; walk-forward 1 and 0 of 7 windows | Drop |
| Cross-sectional momentum top 3, 1-3 months | Coins 1d 2020-25 | Sharpe 0.38 vs 1.07; drawdown -95%; break-even cost 0; walk-forward 0 of 7 windows, out-of-sample Sharpe -0.28 | Drop |
| Candle pattern + MACD turn | BTC, ETH 4h 2020-25 | +0.05R and +0.17R a trade; beat 70% and 88% of random-entry paths (95% needed); Sharpe 0.17 vs 1.07 and 0.56 vs 1.12 | Drop |
| Study: Binance delisting announcement to removal | 80 delisted coins 1d, 21 announcements 2022-10 to 2025-09 | Day 1's open to the removal day's open: -39.8% on average (-41.9% vs BTC), P(< 0) 100%, after -31% on the announcement day; no rebound. EXIT RULE: sell held coins at once | Study passed (exit rule); an exit-rule note waits for an altcoin portfolio |
| Study: shorting the delisting window with perps (hypothetical) | 12 coins with a perp that traded to removal, 6 announcements 2024-02 to 2025-09 | Net at 2x after funding and 2x costs -7.8% (P > 0: 29%); 3 of 12 liquidated (HIFI +1,357%); funding -1.2%; 16 of 28 perps stopped before the spot removal. INSUFFICIENT (10 announcements needed) | No reason to hurry into derivatives for this |
| Order flow Model 2 (YouTube batch 2 C) | ETH, SOL 5m 2020-25 | ETH -81% (Sharpe -3.67, 790 trades, 30% won, -0.68R a trade); SOL -80% (-3.42, 637 trades, -0.73R); negative before costs on both; walk-forward 0 of 7 and 0 of 6; ETH no better than random entries (beat 19% of paths) | Drop (Luke) |
| CHoCH + FVG at the New York open (YouTube batch 1 S2) | SOL 5m 2020-08 to 2025-10 | -40%, Sharpe -0.92, 308 trades, 21% won, -0.55R a trade (about +0.04R before 0.59R of costs); walk-forward 1 of 6; no better than random entries (beat 79% of paths, 95% needed) | Drop (Luke) |
| Set and Forget, mechanical zones (YouTube batch 1 S1) | BTC, ETH 1H 2020-25 | BTC +0.6% (Sharpe 0.04) vs +12% held at the same exposure, 62 trades, +0.02R; ETH -6.2% (-0.18) vs +15%, 59 trades, -0.09R; costs small (0.06-0.07R): no edge before them; walk-forward 0 of 7 and 1 of 7; BTC no better than random entries (beat 50% of paths) | Drop (Luke) |
| Trend-vol-target, unchanged, on gold and currencies (Luke) | PAXGUSDT 1d 2020-08 to 2025-10; EURUSDT 2020-25; GBPUSDT 2020-07 to 2023-12 | Fails on all three. Gold +40% (Sharpe 0.58) vs +56% for gold held at the same exposure (Sharpe 0.93), robust and walk-forward 1 of 6, 0 of 35 start windows; costs were 75 of its 206 USDT before costs, mostly an assumed volume-band spread likely too high for gold, but even near-zero spread would only tie holding. EUR -20% (Sharpe -0.68) and GBP -21% (-0.88), negative before costs. On these markets the 50% volatility target always caps at 100%, so it is the trend votes alone | Drop (Luke) |
| Exploration graduate: BTC regime + coin trend, volatility-sized | 180 confirmation-half altcoins 1d, first bar to 2025-10 | Searched on the other 181 coins; run once here: beat holding's Sharpe on 149 of 180 coins (83%), 427 of 771 walk-forward windows (55%), median Sharpe 0.57. PASS (both above half) | Validated; pursued on ETH and SOL (below) |
| Study: low funding as a next-week coin picker | Exploration half + BTC/ETH/SOL 1d, 234 weeks 2020-09 to 2025-10 | Slope -0.26% a week per SD (P(> 0) 9%); top fifth -0.16% a week vs the average coin; as money -87% after costs (drawdown -98%), the crowded fifth -53%: KILL (the opposite sign to the idea) | Drop (Luke) |
| Study: buyer-led taker flow as a next-week coin picker | Same | Slope +0.17% a week per SD (P 86%, 90% needed); top fifth +0.52% a week vs the average coin (P 96%) but as money -38% after costs (drawdown -92%) against -57% for the average coin and +102% for BTC: KILL | Drop (Luke) |
| Exploration graduate: altcoin crash rebound (-30% in 7 days, hold 3) | 180 confirmation-half altcoins 1d | Beat holding on 111 of 180 coins (62%) but only 274 of 771 walk-forward windows (36%), median Sharpe 0.44. FAIL | Drop (Luke) |
| BTC regime + coin trend on ETH and SOL (Luke's question; not a confirmation) | ETH 1d 2020-25; SOL 1d 2020-08 to 2025-10 | Unchanged rule. ETH Sharpe 1.33 vs 1.12 held, +1,094% vs +398% at the same exposure, drawdown -35%; SOL 1.72 vs 1.24, +1,759% vs +380%, drawdown -32%; both above trend-vol-target on the same coins (1.24, 1.52). Pass edge, start dates (76%, 100%), costs (19x, 29x), trades, drawdown, walk-forward (4 of 7, 4 of 6) and robustness (94% of neighbours on both); fail only luck against the main bar (deflated 1%, 8%). Both coins' holdouts are already spent (trend-vol-target) | Pursue (Claude, delegated by Luke): paper trading |
| Exploration graduate G1: BTC-regime rotation, top 3 of 8 large coins | Own coins 2018-25 (engine); holdout year 2025-11 to 2026-10 on BTC + XRP, SUI, TRX, ADA, AVAX, UNI, ARB | Own coins (note btc-regime-rotation-g1-on-its-own-coins): Sharpe 1.55 vs BTC 0.74, +4,048% vs +149% at the same exposure, drawdown -37%, start dates 70%, costs 23x, robust (100% of 22 neighbours); fails luck (deflated 1%) and walk-forward (5 of 11, out-of-sample Sharpe 1.33 = in-sample). Holdout on unseen coins: +0.0% vs -29.2% for BTC held and -4.7% at its 18% exposure, drawdown -11%: PASS | Pursue (Luke): forward paper |
| Exploration graduate G2: BTC-regime rotation, top 1 of 4 large coins | Own coins 2018-25; holdout year on BTC + XRP, SUI, TRX | Own coins: Sharpe 1.51 vs 0.74, drawdown -36%. Holdout: -22.6% vs -5.7% for BTC at its 22% exposure: FAIL | Drop (Luke) |
| Short side: btc-regime-trend's mirror (hypothetical) | ETH, SOL 1d 2020-01 to 2025-10 | Sharpe 1.30 and 1.35 vs 1.47 and 1.64 long-only; short legs +13% (P 72%) and +6% (P 68%); beat long-only in 1 of 6 years each (2022). DROP by its note | Rejected (Luke) |
| Short side: G1 shorting its 3 weakest coins while BTC is 3% below its 50-day (hypothetical) | G1's 8 coins 1d 2020-01 to 2025-10 | Sharpe 1.13 vs 1.66; short leg -49% (loses before costs); drawdown -54%. DROP | Rejected (Luke) |
| Trend-vol-target long and short, (up - down votes) / 3 (hypothetical) | BTC, ETH, SOL 1d 2020-01 to 2025-10 | Sharpe 0.82, 0.84, 0.97 vs 1.23, 1.28, 1.45 long-only; short legs -46%, -35%, -38%. DROP | Rejected (Luke) |
| Long/short momentum, top 3 vs bottom 3 by 90 days, weekly (hypothetical) | Liquid exploration-half coins + BTC/ETH/SOL with a perp, 2020-01 to 2025-10 | +39%, Sharpe 0.44, mean week +0.13% (P 84%, 95% needed), beta -0.05, positive in 3 of 6 years; long leg +119%, short leg -50%. DROP | Rejected (Luke) |
| Cash-and-carry: spot long, perp short while 7-day funding is positive (hypothetical) | BTC, ETH, SOL 2020-01 to 2025-10 | +6.6% a year on equity, P 100%, 5 of 6 years positive, drawdown -6.6% (basis not modelled), 53 trades, 14 margin calls: NEEDS-SHORTING (passes every test; needs a real perp) | Needs shorting (Luke) |
| Study: a coin's first perp listing | 102 exploration-half coins (+ BTC/ETH/SOL), perps first listed 2020-01 to 2025-09 | 30 days after vs the other coins: -15.2% (P(< 0) 100%, 75% below zero), below all 200 placebo draws (median -1.3%); +100% in the 30 days before. Median-coin benchmark -5.5%, BTC -7.9%: EXIT RULE | Validated (Luke); a rule for the altcoin portfolio |
| Short new perp listings for 30 days, hedged with BTC (hypothetical) | Same 102 events, 3 slots, 2020-01 to 2025-10 | +11%, Sharpe 0.25, mean trade +2.3% (P 80%), 3 of 6 years positive, drawdown -56%; 15 squeeze close-outs, funding -18% of equity, 29 events skipped; unhedged -68%. DROP | Rejected (Luke) |
| Study: large token unlocks (2%+ of unlocked supply) | 122 unlocks on 12 exploration-half coins, 2020-25 (DefiLlama schedules) | 30 days before vs the other coins: +0.4% (P(< 0) 47%); the same coins on random days -4.5%: KILL. Insiders -7.1% (7 events), large-for-volume -2.1% vs +2.6% | Rejected (Luke) |
| Altcoin portfolio: G1's rule on a point-in-time universe, with delisting and new-perp exits | Exploration-half coins 1d, 2019-01 to 2025-10 (2019 cash) | +101%, Sharpe 0.53 vs 0.44 for the large-basket control (pass) but 1.10 for BTC at the same 17% exposure; neighbours 0.46-0.70; walk-forward 3 of 9; 2-year starts 32%; drawdown -33%; luck 0% | Drop (Luke) |
| Tori Trades trendline method, long only (Luke's request; chart pattern, disclosed) | BTC, ETH, SOL 4h, 2020-25 | +26/+45/+65%, Sharpe 0.41/0.57/0.82 vs 1.07/1.12/1.24 for the coin at the same 6-10% exposure; +0.17R to +0.40R a trade; drawdowns -18% to -25%. Fails test 1 on all three | Drop (Luke) |
| Study: US buying on Coinbase (USDT-adjusted premium above normal) | BTC, ETH 1d 2021-25 | Spread +0.67% a week, P 79%; +1.42% after falling weeks, -0.35% after rising ones (control fails) | KILL; rejected (Luke) |
| Study: high variance premium (DVOL less realised) | BTC, ETH 1d 2021-25 | Spread +0.73%, P 81%; +1.45% after falls, -0.20% after rises (control fails) | KILL; rejected (Luke) |
| Study: dollar net liquidity growing | BTC, ETH 1d 2018-25 | Spread -0.71%, P 17%; negative after rises and falls | KILL; rejected (Luke) |

Each strategy was judged by its own note's pass test, written before it ran. From
2026-11-14 every strategy is judged on one scorecard instead.

## Mistakes in the process, and what changed

- **A study read across a token swap** (2026-11-18). The event-study grid joined a coin's
  separate histories into one column, so the perp-listing study's first run measured
  DREP's 100:1 token swap (2021-04, after a one-day gap) as a +38,667% month in the
  benchmark, and reported -25.4% instead of -15.2%. Found by checking why 2021 read -78.6%.
  Fixed (no window crosses a break in a coin's history; tested), the run voided and rerun.
  Checked: the five shorting studies need a year of unbroken history and findings 14 never
  made DREP eligible, so neither was affected.

- **Duplicate bars in Binance's archive (found 2026-11-15).** The daily files for AXSUSDT
  2026-02 (the 10th) and UMAUSDT 2026-07 (the 15th) each held one day twice, identical in
  every column. The portfolio engine refused the coin basket until it was fixed; the extra
  copies were removed by hand and recorded here. No other daily file has duplicates. Both
  days are in the holdout, so no earlier result was affected.

- **Spread bands on short bars read bar volume as daily volume (found 2026-11-16).** A coin
  without its own config entry (anything but BTC and ETH) paid the band for the median
  volume of its last 30 *bars*; the bands are set for daily volume, so on 5m bars SOL
  read as a tiny coin and paid about 8 bps a side instead of its tier. Found because a
  SOL 5m run paid 49 bps a round trip against the note's 17. Fixed (shorter bars use the
  30 complete days before their own); the two SOL 5m runs were voided and rerun, both
  still failing. Four exploratory SOL trend runs on 4h and 1h (2026-11-12) were also
  overcharged; nothing on daily bars, BTC or ETH was affected.
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
  correctly. It happened again on 2026-11-16 (629 and a luck bar of 2.12 rather than 751
  and 2.23): a script opened the run store one folder too deep and found no runs. Caught
  while building the Research tab; CLAUDE.md now names the right call.
- **The paper runner didn't refresh markets a strategy reads (found 2026-11-16, before any
  account used one).** btc-regime-trend reads BTC's close; a run updated only the traded
  coin, so a day's missing BTC bar would have read as "unknown", meaning cash, and sold the
  position on missing data. Every input market is now refreshed, and a live run refuses to
  decide on a missing input.

## What these results can't tell you

- **Costs are modelled, not measured.** There is no order-book history, so the spread is an
  estimate and market impact follows a textbook square-root law. Paper trading (P4) will
  measure real fills.
- **One exchange, spot only, long only.** Binance perpetuals aren't available to Australian
  retail clients, so shorting and funding strategies are out of scope.
- **Mostly one market cycle.** 2020-25 contains one full bull and bear market in crypto.
  Results from 2017-19 were sometimes different, as weekly momentum showed.
- **Daily and 4-hour bars.** At 330 USDT, costs rule out anything faster.
- **The holdout is spent for BTC, ETH and SOL**, used for trend-vol-target (passed) and
  weekly momentum and the rotations (failed), and for XRP, SUI, TRX, ADA, AVAX, UNI and ARB,
  used by the two rotation graduates' confirmation (2026-11-16). It is unused for every
  other coin.
- **Exploration results are not evidence.** Numbers in research/exploration/log.md were
  found by searching; only the confirmation run and later pre-registered tests count.
