# To-do

The active list. Claude keeps it up to date in the same commit as the work (see CLAUDE.md).
Rules live in CLAUDE.md; results in `research/findings.md`; ideas not yet scheduled in
`research/backlog.md`. Older history is in git.

## Running
- Paper trading on Binance's Spot Testnet, hourly at 10 past (launchd on the Mac):
  - tvt-btc, tvt-eth, tvt-sol: trend-vol-target, opened 2026-11-15.
  - brt-eth, brt-sol: btc-regime-trend, opened 2026-11-16.
  - rot-g1: btc-rotation-8 (G1, a basket of 8 coins), opened 2026-11-16.
  - scs-btc, scs-eth: stablecoin-vol-hold (watch, not pursue), opened 2026-11-22.
  Each runs 395 days from its first decision (the 2026-11-14 or 15 bar). Reviews every 3
  months (`kestrel paper track`; first around 2027-02-15), a failure-only check at 182 days
  (around 2027-05-15), verdicts around 2027-12-14. Runs are late or missed while the Mac
  sleeps (a missed day is recorded, never traded late).

## Waiting on Luke
- Reject note `trend-vol-target-out-in-the-4h-volatile-regime`? Its 6 runs (2026-11-23)
  failed its pre-registered test on all 3 coins (findings 24). Waiting on Luke's say-so.
- GitHub Actions won't start jobs (billing / spending limit, 2026-11-22): CI is down; the
  local `scripts/check.sh` is the gate until it's fixed.

## Next
- [ ] Pick the next idea by independence from the trend bet (news tone first): expected overlap
      written in the note, measured overlap reported beside its scorecard.
- [ ] Monitoring-tag drift: blocked on data. Binance's announcement feed holds 1 tag
      announcement in ~2,700 scanned (2026-11-18); the history would need another source.

## Shorting research (hypothetical; Luke, 2026-11-16)
Nothing here can be traded yet: retail shorting in Australia is CFDs at 2:1 (Eightcap,
Swyftx Leverage), and real perps are wholesale-only (Coinbase AU). Each is a frozen note,
its runs count as trials, costed both ways: an Eightcap-style CFD (spread plus a nightly
financing charge on both sides) and a Binance perp (its real funding). A result that would
be pursue or watch is recorded as **needs-shorting** (add the decision value when the first
one lands: `kestrel.decisions` and the Research tab), never traded until a licensed route
exists and Luke has checked the Swyftx employee trading policy.
- [ ] Delisting short: the open note `shorting-the-delisting-window-with-perps` was
      INSUFFICIENT at 6 announcements (10 needed). Re-run once 10 exist, rather than reject.
- [ ] Before each: a cheap check that the account size works (CFD minimums, margin at 2:1
      on about 330 USDT).

## Parked
- Exploration round 2 (Luke, 2026-11-22: not this design). Proposed: a new coin split, the
  confirmation half read only on data from 2026-11 onward, first run 2027-11. Pre-holdout data
  is searched almost everywhere and the holdout year can't be used, so time forward is the
  only fresh data; revisit with a different design if wanted.
- The two exit rules (Luke, 2026-11-21): sell a coin when its delisting is announced
  (findings 11); don't hold one through its first month of perps (findings 18). Both built
  into the dropped altcoin portfolio (`kestrel.data.coin_events`); nothing pursued holds
  small enough coins to need them. Revive with any strategy that holds small coins. Before
  real money: a delisting announcement on a held coin halts that paper account for Luke.
- Always-on machine for paper trading (Luke, 2026-11-16: not for now). The "Pi" turned out
  to be a microcontroller, so the candidate is the TrueNAS server (SCALE: a container; its
  own user, keys in a file only Kestrel reads, no open ports, nightly ledger backups, one-step
  cutover, never two machines on one account). Needs first: a dependency lock file with
  hashes (done 2026-11-21). Recording Binance's liquidation and order-book streams waits on this too.

## Later
- [ ] Keys from the macOS keychain (or the server's key file), not ~/.zshrc: before any live
      (non-testnet) keys exist. Luke adds them himself; Claude never sees the values. Live
      keys: trading only, withdrawals off, restricted to the home IP.
- [ ] News-tone study (`kestrel signal-study news-tone`): downloading. GDELT throttles this
      address, so launchd (`~/Library/LaunchAgents/com.kestrel.news.plist`, log
      `data/news/schedule.log`) runs `kestrel ingest-news BTCUSDT ETHUSDT --max-months 2`
      hourly at :40: gently since 2026-11-22 (one try per month, no retries, the coin
      furthest behind first; a refusal waits for the next hour). By 2026-11-22 BTC had
      2017-01..2018-11, ETH 2017-01..2018-01. When both reach 2025-10: unload it
      (`launchctl bootout gui/$(id -u)/com.kestrel.news`), then draft the note. If it
      stalls for days: GDELT's BigQuery tables (needs a Google Cloud account).
- [ ] Expectancy by conviction bucket (P2 leftover), once a strategy varies its sizing.

## Done recently
- 2026-11-23 4h regime filter on trend-vol-target run (findings 24): worse on BTC, ETH and SOL.
  4h regime fits stored (`kestrel regimes --interval 4h --save`).
- 2026-11-22 Regimes compared (findings 23): the preset stays (more states only track volatility
  more finely); a 4h model warns of daily volatile spells about 40 hours early, 3 times in 4
  falsely. Non-trend notes rejected (Luke).
- 2026-11-22 Three non-trend signal studies (findings 22): Coinbase premium, variance
  premium, dollar liquidity. All KILL under their past-week control; rejecting the notes waits
  for Luke. Outside series: `kestrel ingest-external` (Coinbase, Deribit DVOL, FRED).
- 2026-11-22 Decay check (rule agreed with Luke): after 90 paper days, a model replay below the
  5th percentile of its own backtest's bootstrapped Sharpe is flagged (`paper track`, the
  account view, an alert). All accounts too early until about 2027-02.
- 2026-11-22 Stablecoin hold (watch), Luke's request: its one holdout look, volatility-sized
  version: BTC passes (-7.7% vs -14.7% held at 53%), ETH fails (-28.2% vs -18.1% at 46%); a
  split, stays watch. Paper accounts scs-btc and scs-eth opened; the paper runner now refreshes
  DefiLlama's supply each run (appended, never rewritten: the history is revised after the fact).
- 2026-11-22 House style (Luke): an institutional frame (navy bar with a gold rule, cream,
  Libre Baskerville titles and figures, square corners) with Source Serif reading text on
  the story pages; light and dark. Fonts bundled, nothing loaded from outside. README
  screenshots retaken (a real backtest's scorecard; the Thesis in dark).
- 2026-11-22 Guided tour of the dashboard ("Tour", top right): 12 stops from the question to
  the limits, one of them a strategy failing its scorecard. Our own component; the stops
  are data, tested to point at things that exist. Later, only if wanted: a hosted
  read-only snapshot so employers can open it without the Mac.
- 2026-11-22 Dashboard: a Thesis page (the research summary, top right by the API status);
  findings on the Rules page as one-line titles that open for detail. Fixed: findings 13-21
  were glued onto 12 (their wrapped titles didn't parse); the parser now fails loudly.
- 2026-11-22 A one-page research summary for employers (`docs/research-summary.md`), linked
  from the README; README status and findings intro brought up to date.
- 2026-11-22 Paper alerts: a macOS notification for a halt, a failed run, falling behind, a
  missed day or a trade (`kestrel paper run --alerts`; the six launchd jobs rewritten and
  reloaded). Phone push or email would need an outside service: not added.
- 2026-11-22 Trading page: "Waiting for" on every account, and each account's entry tests
  (close, level, the move needed by the next close). Tested against the strategies' own
  decisions (`kestrel.strategies.conditions`).
- 2026-11-22 Why G1 works (exploration, `research/exploration/why_g1.py`, log): the BTC
  filter is the edge in 2018-21; in 2022-25 no version beats BTC held (G1 0.66 vs 0.69),
  the ranking only beats random picks. Risk-adjusted ranking doesn't help.
- 2026-11-22 Tokenized stocks excluded from the coin universe by Binance's `bStocks` tag
  (104 pairs) and XAUT as gold: 700 coins to 595. None first traded before the holdout, so
  no research result changes.
- 2026-11-21 Tori Trades trendline method tested (Luke's request): fails test 1 on BTC, ETH,
  SOL; dropped (Luke).
- 2026-11-21 Decisions (Luke): altcoin portfolio dropped; token-unlock note rejected;
  cash-and-carry validated and recorded as needs-shorting (a new decision value, shown on
  the Overview and Research tab); independence reported as information on new strategies.
- 2026-11-21 Dashboard at phone width (checked at 390px: no sideways scroll; the menu wraps,
  stat tiles go two a row). Strategies page re-fetches when the tab regains focus. The Binance venue
  accepts fees paid in BNB (valued at BNB's price); testnet fills charge none.
- 2026-11-21 CI trimmed for the free Actions minutes (one Python, docs-only pushes skipped,
  superseded runs cancelled); dependencies locked with hashes (`requirements-dev.lock`).
- 2026-11-16 Five hypothetical shorting studies (findings 17): trend shorts and long/short
  momentum failed (rejected, Luke); cash-and-carry passed.
- 2026-11-16 rot-g1 opened (G1 pursued, G2 dropped; Luke); its paper note closed as paper.
- 2026-11-16 Basket paper trading on the Binance testnet: a multi-coin venue, reconciliation
  summed over every testnet account, a basket kill switch, `paper track` and the Trading
  page for baskets. A missing open never forces a sale (the run waits).
- 2026-11-16 Strategy overlap report (`kestrel overlap`, findings 16): the pursued strategies
  are mostly one bet, long crypto while it trends; correlation follows the coin. G1's
  survivorship disclosed (findings 15, decisions).
- 2026-11-16 Basket paper trading (simulated venue): the portfolio engine and the paper
  runner share one day step (`kestrel.execution.basket`); a day-by-day replay reproduces
  the backtest's fills and marks exactly.
- 2026-11-16 Docs cleaned up: README, TODO, CLAUDE.md, findings, backlog, exploration log.
  Four finished notes closed (paper or validated).
- 2026-11-16 Overview: one "What to pursue" entry per strategy, with its coins.
- 2026-11-16 Paper accounts brt-eth and brt-sol opened (btc-regime-trend, pursue). The
  paper runner refreshes every market a strategy reads and refuses missing inputs.
- 2026-11-16 Funding and taker-flow cross-sectional studies: both KILL; rejected (Luke).
- 2026-11-16 btc-regime-trend on ETH and SOL: every scorecard test but luck; pursue (Claude,
  delegated by Luke).
- 2026-11-16 Exploration experiment round 1: btc-regime-trend PASSED confirmation on 180
  unsearched coins, crash rebound FAILED; method kept (Luke). Research tab.
- 2026-11-16 Trend-vol-target on gold, EUR, GBP: fails all three; dropped (Luke).
- 2026-11-16 YouTube leftovers (order flow, CHoCH + FVG, Set and Forget): all fail; dropped.
  Engine: resting limit orders; spread bands on sub-daily bars fixed.
- 2026-11-16 Delisting short via perps (hypothetical): INSUFFICIENT.
- 2026-11-15 Delisting study: EXIT RULE. Project moved to ~/kestrel; paper jobs reloaded.
- 2026-11-15 Trend-vol-target pursued on ETH and SOL after their holdouts; paper trading
  opened for all three coins.
