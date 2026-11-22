# Kestrel research: a one-page summary

Kestrel is a systematic crypto trading platform (Binance spot, long only, a 330 USDT
account) built to answer one question honestly: is there a rule that beats simply holding
the coin, after costs, once you account for how many rules were tried?

**Short answer, as of 2026-11-22: not yet proven.** 88 pre-registered research notes and
866 counted trials later, no strategy clears the luck test. Three survive every other test
and are being paper-traded forward on Binance's Spot Testnet. The detail behind every
number here is in [research/findings.md](../research/findings.md).

## How a result is judged

- **Pre-registered.** Each idea is written up and frozen (hypothesis, exact rules, who pays
  the profit and why, what would falsify it) before it sees market data. A change after a
  result is a new version, and its runs count.
- **Costs on every fill, at the real size.** Fee, a spread estimated from each coin's
  volume, square-root market impact, and Binance's 5 USDT minimum order, on a 330 USDT
  account. A BTC round trip costs about 22 bp.
- **No lookahead.** Decide at a bar's close and fill at the next open. Every indicator is
  checked by recomputing it on truncated histories.
- **The right benchmark.** A strategy in cash half the time halves its drawdown for free.
  So it must beat *the same coin held at the same average exposure* on Sharpe. Lower
  drawdown alone is not an edge.
- **Every trial counted.** Every setting tried, grids included, goes into a trial log. The
  deflated Sharpe (Bailey and López de Prado) asks whether a result beats the best of that
  many worthless rules. At 866 trials, luck alone would give an annual Sharpe of about
  2.28.
- **A holdout year (2025-11 to 2026-10)**, fixed before any research and read once per
  finished strategy.
- **Explore, then confirm.** Coins were split once, at random, before anyone looked. One
  half can be searched freely (never evidence). Graduates run once on the locked half
  under a test fixed in advance.
- **One scorecard** of nine tests for every strategy: edge, luck, walk-forward, start
  dates, break-even cost, trade count, drawdown limit, neighbouring settings, and random
  entries for bracket strategies.

## What is being paper-traded, and why it isn't proven

| Strategy | Passed | Failed |
|---|---|---|
| **Trend following with volatility targeting**: three trend votes (20/60/120 days) set the size, scaled down when volatility is high. BTC, ETH, SOL. | Beats the coin at the same exposure on all three (Sharpe 1.21 vs 1.07 on BTC, 2020-25); passed the holdout year on all three (BTC -2.4% vs -8.9%) | Luck (deflated 28-59%); walk-forward (2 of 7 windows) |
| **BTC regime rule**: hold an altcoin while BTC is above its 50-day average and the coin is up over 30 days. ETH, SOL. | Found on one half of the altcoins, confirmed on the unsearched half (149 of 180 coins); on ETH and SOL every test but luck | Luck (deflated 1-8%); both coins' holdout years were already spent |
| **G1 rotation**: weekly, while BTC is 3% above its 50-day average, hold the top 3 of 8 large coins by 90-day return. | Holdout year on 7 coins it had never seen: flat while BTC fell 29% (-4.7% at its exposure) | Luck, walk-forward; its 8 coins were chosen in 2026, so the 2018-25 backtest is survivorship-biased (disclosed) |

All three are one bet: long crypto while it trends. Their daily returns correlate 0.35-0.83,
and all earn only in BTC's rising months (`kestrel overlap`). Taking G1 apart showed
something more: in 2022-25 none of its versions beat holding BTC (Sharpe 0.66 vs 0.69).
Most of its backtest edge is the 2018-21 altcoin boom. The forward record decides, with a
first review in February 2027 and verdicts in December 2027.

## What failed, by family

| Family | Examples | Why it failed |
|---|---|---|
| Chart patterns and reversals | Trendline breaks and bounces, wedges, pivots, level retests, a YouTube trader's trendline method | No better than zero after costs, worse than a plain breakout, or below holding at the same tiny exposure |
| Dip-buying | RSI dips, big down days, Fear & Greed contrarian, crash rebounds | In crypto, moves carry on: buying weakness kept losing |
| Intraday setups (1m-5m) | Order flow, change of character + fair value gap | A 22 bp round trip is 0.5-3.9R on stops that tight; a cost check before any build killed most |
| Altcoin selection | Cross-sectional momentum, 52-week high, funding, taker flow, an altcoin portfolio | The average liquid altcoin lost about 57% in 2020-25; picking among them lost to a small BTC position |
| Additions to survivors | Stops, a regime filter, stablecoin votes, a short side | Each made the survivor worse: its trend votes already do the job, and crypto's rebounds take back what shorts make |

Some event studies found real effects that are only safety rules for a long-only account:

- after a delisting announcement, coins fell another 40% on average before removal;
- after a coin's first perp listing, it lagged other coins by 15% over the next month.

As shorts, both effects were eaten by squeezes and funding. The one short-side idea that
passed every test is cash-and-carry: about +6.6% a year collecting funding. It needs a real
perpetual future, which Australian retail can't use, so it is recorded as
`needs-shorting` and never traded.

## Mistakes caught along the way

- **A token swap in a study.** A study read across DREP's 100:1 token swap as a +38,667%
  month. Since then no window crosses a break in a coin's history; the run was voided
  and rerun.
- **Spread bands on short bars.** Bands on short bars read bar volume as daily volume and
  overcharged SOL on 5m. Fixed, and the affected runs rerun.
- **A missed input in the paper runner.** It would have read a missing BTC bar as "cash"
  and sold on bad data. Every input market is now refreshed, and a missing one stops the
  run.
- **A hidden check failure.** Piping the check through `tail` hid a failure. One
  script now stops at the first failure.

Each is in findings.md with what it touched.

## What these results can't tell you

- Costs are modelled, not measured; paper fills will measure them.
- 2020-25 is mostly one crypto cycle.
- The holdout is spent for the major coins.
- Exploration numbers are never evidence.
- It may be that nothing here is an edge. That is an acceptable result.

## Where to look

| What | Where |
|---|---|
| The rules this research follows | [CLAUDE.md](../CLAUDE.md), "Research rules" |
| Every result and the full table | [research/findings.md](../research/findings.md) |
| The decision on each strategy | [research/decisions.toml](../research/decisions.toml) |
| The frozen notes | [research/strategies/](../research/strategies/) |
| The exploration log | [research/exploration/log.md](../research/exploration/log.md) |
| The scorecard | `src/kestrel/validation/scorecard.py` |
| The deflated Sharpe | `src/kestrel/validation/deflated.py` |
| The paper runner | `src/kestrel/execution/paper.py` |

1,048 backend and 207 dashboard tests run on every change. Among them: the two backtest
engines are tested against each other, and a day-by-day paper replay must reproduce the
backtest exactly.
