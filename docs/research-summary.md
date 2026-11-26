# Kestrel research: a one-page summary

Kestrel is a systematic trading research platform built to answer one question honestly:
is there a rule that beats simply holding, after costs, once you account for how many rules
were tried? It started with crypto (Binance spot, long only, a 330 USDT account) and in
November 2026 added a second market: forex, long and short, through a retail CFD broker.

**Short answer, as of 2026-11-23: not yet proven.** 97 pre-registered research notes and
876 counted trials later (872 in crypto, 2 in its confirmation pool, 2 in forex), no
strategy clears the luck test. Three crypto strategies survive every other test and are
being paper-traded forward on Binance's Spot Testnet. Both forex strategies tested so far
failed. The detail behind every number here is in
[research/findings.md](../research/findings.md).

## How a result is judged

- **Pre-registered.** Each idea is written up and frozen (hypothesis, exact rules, who pays
  the profit and why, what would falsify it) before it sees market data. A change after a
  result is a new version, and its runs count.
- **Costs on every fill, at the real size.** Crypto: fee, a spread estimated from each
  coin's volume, square-root market impact, and Binance's 5 USDT minimum order (a BTC round
  trip costs about 22 bp). Forex: the real bid and ask from the data, and financing every
  night, including a broker markup measured from the broker's own rates.
- **No lookahead.** Decide at a bar's close and fill at the next open. Every strategy is
  checked by recomputing it on truncated histories.
- **The right benchmark.** A strategy in cash half the time halves its drawdown for free,
  so a crypto strategy must beat *the same coin held at the same average exposure* on
  Sharpe. Forex has no buy-and-hold, so its benchmark is holding nothing.
- **Every trial counted, per market.** Every setting tried, grids included, goes into a
  trial log. The deflated Sharpe (Bailey and López de Prado) asks whether a result beats
  the best of that many worthless rules. Each market has its own pool, fixed before its
  first result, and every report also gives the total across pools. Crypto's bar is now an
  annual Sharpe of 2.27.
- **A holdout per market, fixed before any research:** crypto from 2025-11, forex from
  2023-01. Each is read once per finished strategy.
- **Explore, then confirm.** Crypto coins were split once, at random, before anyone looked.
  One half could be searched freely (never evidence). Graduates ran once on the locked half
  under a test fixed in advance.
- **One scorecard** for every strategy: edge, luck, walk-forward, start dates, break-even
  cost, trade count, drawdown limit, neighbouring settings, and random entries for bracket
  strategies.

## How each safeguard is enforced

Each rule above is enforced by code, and each has a test that fails if it is broken.

| Safeguard | Enforced in | Tested by |
|---|---|---|
| Only frozen notes run | `ResearchStore.require_testable`, `run_target` (`src/kestrel/research.py`) | `tests/test_cli.py::test_backtest_research_requires_a_frozen_note`, `tests/test_api_research.py::test_a_draft_cannot_be_backtested` |
| No lookahead | `check_no_lookahead` (`src/kestrel/strategy.py`), `validated_fx_weights` (`src/kestrel/fx_strategy.py`): recompute on cut histories, every earlier weight must be unchanged | a `test_no_lookahead...` per strategy; a strategy that peeks at tomorrow is caught in `tests/test_fx_backtest.py` |
| Holdout locked | `check_inputs` raises `HoldoutError` (`src/kestrel/backtest/engine.py`); `run_fx` and `fx_backtest` refuse forex holdout data | `tests/test_cli.py::test_backtest_refuses_holdout`, `tests/backtest/test_fx_engine.py::test_refusals` |
| Every trial counted, per market | `trial_set`, `pool_of`, `every_pool` (`src/kestrel/validation/deflated.py`) | `tests/test_deflated.py`, `tests/test_fx_backtest.py::test_forex_runs_have_their_own_pool` |
| Confirmation half locked | `check_symbols` (`src/kestrel/exploration.py`) | `tests/test_exploration.py::test_the_lock_refuses_only_confirmation_coins_until_unlocked` |
| Engines agree | three engines, one model | `tests/backtest/test_parity.py` (event against vectorised), `tests/backtest/test_fx_engine.py::test_parity_with_the_crypto_engine_long_only_and_unlevered` |
| Paper trading reproduces the backtest | one rebalance step shared by both (`src/kestrel/execution/rebalance.py`); `src/kestrel/execution/tracking.py` replays every account | `tests/test_paper.py::test_a_day_by_day_replay_reproduces_the_backtest`, `tests/test_paper_basket.py` |
| Collapse caught early | `src/kestrel/validation/decay.py`: after 90 days, an account's replay against its own backtest's 1-in-20 floor | `tests/test_decay.py` |
| Bad data reported, never repaired | `check_klines` (`src/kestrel/data/quality.py`), `fx.check` (`src/kestrel/data/fx.py`); a pair with integrity errors isn't saved | `tests/test_fx_data.py::test_problems_are_reported_not_repaired` |
| The scorecard | `src/kestrel/validation/scorecard.py` | `tests/test_scorecard.py::test_each_threshold_fails_on_its_own` |

## What is being paper-traded, and why it isn't proven

| Strategy | Passed | Failed |
|---|---|---|
| **Trend following with volatility targeting**: three trend votes (20/60/120 days) set the size, scaled down when volatility is high. BTC, ETH, SOL. | Beats the coin at the same exposure on all three (Sharpe 1.21 vs 1.07 on BTC, 2020-25); passed the holdout year on all three (BTC -2.4% vs -8.9%) | Luck (deflated 0-4% at 880 trials on 2026-11-26; 28-59% when first measured against fewer); walk-forward (2 of 7 windows) |
| **BTC regime rule**: hold an altcoin while BTC is above its 50-day average and the coin is up over 30 days. ETH, SOL. | Found on one half of the altcoins, confirmed on the unsearched half (149 of 180 coins); on ETH and SOL every test but luck | Luck (deflated 1-9% at 880 trials, 2026-11-26); both coins' holdout years were already spent |
| **G1 rotation**: weekly, while BTC is 3% above its 50-day average, hold the top 3 of 8 large coins by 90-day return. | Holdout year on 7 coins it had never seen: flat while BTC fell 29% (-4.7% at its exposure) | Luck, walk-forward; its 8 coins were chosen in 2026, so the 2018-25 backtest is survivorship-biased (disclosed) |

A fourth, holding BTC or ETH while stablecoin supply grows (sized by volatility), is also
paper-traded but only as *watch*: it failed most tests, then passed its holdout year on BTC
and failed it on ETH.

All three main strategies are one bet: long crypto while it trends. Their daily returns
correlate 0.35-0.83, and all earn only in BTC's rising months (`kestrel overlap`). Taking G1
apart showed something more: in 2022-25 none of its versions beat holding BTC (Sharpe 0.66
vs 0.69). Most of its backtest edge is the 2018-21 altcoin boom.

Eight accounts run hourly. So far every account's decisions match its replay, day for day.
The first decay checks are due in February 2027, and verdicts in December 2027.

## Forex: a second market, two clean negatives

Forex was added to test ideas where they have their best published evidence, with its own
data (OANDA's daily bid and ask from 2005), its own engine (signed and leveraged positions,
nightly financing, profit and loss in AUD), its own holdout and its own trial pool. Both
textbook ideas were run once, at their published settings, 2006-2022:

| Strategy | Result | Why |
|---|---|---|
| **Trend**: 12-month time-series momentum across 10 pairs (Moskowitz, Ooi and Pedersen 2012) | -30%, Sharpe -0.07 | About +2.3% a year before costs. Spreads and a ~1% financing markup on a book averaging 2.8x the account took more. |
| **Carry**: hold the higher-yielding currency where the rate gap beats the markup (Lustig, Roussanov and Verdelhan 2011) | -50%, Sharpe -0.37 | It earned its interest (+103 AUD), then lost about 350 on prices in the 2007-08 carry crash and never recovered while rates sat near zero. |

The lesson is about cost structure: at retail CFD terms, a forex strategy must earn more
than its leverage times the broker's markup every year before it earns anything. Leverage
multiplies that drag as well as the returns. Forex research is paused; fishing through more
forex ideas would only repeat the luck problem in a new pool.

## What failed in crypto, by family

| Family | Examples | Why it failed |
|---|---|---|
| Chart patterns and reversals | Trendline breaks and bounces, wedges, pivots, level retests, a YouTube trader's trendline method | No better than zero after costs, worse than a plain breakout, or below holding at the same tiny exposure |
| Dip-buying | RSI dips, big down days, Fear & Greed contrarian, crash rebounds | In crypto, moves carry on: buying weakness kept losing |
| Intraday setups (1m-5m) | Order flow, change of character + fair value gap | A 22 bp round trip is 0.5-3.9R on stops that tight; a cost check before any build killed most |
| Altcoin selection | Cross-sectional momentum, 52-week high, funding, taker flow, an altcoin portfolio | The average liquid altcoin lost about 57% in 2020-25; picking among them lost to a small BTC position |
| Additions to survivors | Stops, regime filters (daily and 4-hourly), stablecoin votes, a short side | Each made the survivor worse: its trend votes already do the job, and crypto's rebounds take back what shorts make |
| Signals from outside the price | Coinbase premium, the options variance premium, US dollar liquidity | None picked better weeks once the past week's direction was controlled for |

Some event studies found real effects that are only safety rules for a long-only account:

- after a delisting announcement, coins fell another 40% on average before removal;
- after a coin's first perp listing, it lagged other coins by 15% over the next month.

As shorts, both effects were eaten by squeezes and funding. The one short-side idea that
passed every test is cash-and-carry: about +6.6% a year collecting funding. It needs a real
perpetual future, which Australian retail can't use, so it is recorded as
`needs-shorting` and never traded.

## Mistakes caught along the way

- **A token swap in a study.** A study read across DREP's 100:1 token swap as a +38,667%
  month. Since then no window crosses a break in a coin's history; the run was voided and
  rerun.
- **Spread bands on short bars.** Bands on short bars read bar volume as daily volume and
  overcharged SOL on 5m. Fixed, and the affected runs rerun.
- **A missed input in the paper runner.** It would have read a missing BTC bar as "cash"
  and sold on bad data. Every input market is now refreshed, and a missing one stops the
  run.
- **A hidden check failure.** Piping the check through `tail` hid a failure. One script now
  stops at the first failure.
- **Paper jobs colliding.** Three accounts refresh ETH at the same minute, and a shared
  temporary file name let one job move another's file away (17 crashed runs, no wrong
  trades). Every write now uses its own temporary file; a test fires 64 writes at once.
- **A volatility window in days, not bars.** A 4-hour regime model briefly used a 120-bar
  window instead of the study's 20. Caught before any result; the fits were redone.

Each is in findings.md with what it touched.

## What these results can't tell you

- Costs are modelled, not measured; testnet fills aren't the real market's, so live costs
  are still unknown.
- 2020-25 is mostly one crypto cycle.
- The crypto holdout is spent for the major coins.
- Exploration numbers are never evidence.
- It may be that nothing here is an edge. That is an acceptable result.

## Where to look

| What | Where |
|---|---|
| The rules this research follows | [docs/rules.md](rules.md), "Research rules" |
| Every result and the full table | [research/findings.md](../research/findings.md) |
| The decision on each strategy | [research/decisions.toml](../research/decisions.toml) |
| The frozen notes | [research/strategies/](../research/strategies/) |
| The exploration log | [research/exploration/log.md](../research/exploration/log.md) |
| The scorecard | `src/kestrel/validation/scorecard.py` |
| The deflated Sharpe and trial pools | `src/kestrel/validation/deflated.py` |
| The forex engine | `src/kestrel/backtest/fx_engine.py` |
| The paper runner | `src/kestrel/execution/paper.py` |

1,130 backend and 247 dashboard tests run on every change.
