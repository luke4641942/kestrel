# To-do

The active list. Claude keeps it up to date in the same commit as the work (see CLAUDE.md).
Rules live in CLAUDE.md; results in `research/findings.md`; ideas not yet scheduled in
`research/backlog.md`. Older history is in git.

## Running
- Paper trading on Binance's Spot Testnet, hourly at 10 past (launchd on the Mac):
  - tvt-btc, tvt-eth, tvt-sol: trend-vol-target, opened 2026-11-15.
  - brt-eth, brt-sol: btc-regime-trend, opened 2026-11-16.
  Each runs 395 days from its first decision (the 2026-11-14 or 15 bar). Reviews every 3
  months (`kestrel paper track`; first around 2027-02-15), a failure-only check at 182 days
  (around 2027-05-15), verdicts around 2027-12-14. Runs are late or missed while the Mac
  sleeps; the Raspberry Pi item fixes that.

## Waiting on Luke
- [ ] G1 `btc-rotation-8` pursued, G2 dropped (Luke, 2026-11-16). Paper note
      `paper-trading-btc-regime-rotation-g1` drafted for Luke to freeze. The paper runner
      trades a basket on the simulated venue; before rot-g1 opens on testnet: a multi-coin
      Binance venue (reconciles every holding), basket `paper track` and Trading page.
- [ ] Raspberry Pi: which model and RAM, is it running anything, SD card or USB SSD?
      Then the Pi item below.
- [ ] Delisting exit rule (findings 11): write a note (sell a held coin at the next open
      after its delisting is announced), or park it. Only matters for a strategy that holds
      small coins, so it belongs with the altcoin portfolio below.
- [ ] `shorting-the-delisting-window-with-perps` (INSUFFICIENT) is the one open study note:
      reject it, or leave it open for when shorting is possible.
- [ ] Proposal (outside review, 2026-11-16): add "independence" to the research process,
      information only (never a pass test): correlation with the pursued strategies, in BTC
      drawdowns too, and its return after removing BTC exposure. A rule change, so Luke's call.

## Next
- [ ] Move paper trading to the Raspberry Pi (always on). Python environment, a systemd
      timer (teach `kestrel paper schedule` to write one on Linux), keys in a file only
      the Kestrel user can read, a read-only deploy key, releases by tag, nightly ledger
      backup. Cut over in one step: copy `data/paper/` and the BTC/ETH/SOL daily bars,
      unload the Mac's launchd jobs, confirm the first hour matches. Never two machines
      on one account. Harden it: SSH keys only, firewall, no open ports, own user.
- [ ] A dependency lock file with hashes (supply chain: needed before any server deploy).
- [ ] An altcoin portfolio built on btc-regime-trend (up to 3 coins at 330 USDT): which
      coins (a picker inside the regime rule; findings 14 showed pickers alone lose with
      the market), the delisting exit rule, a note, the scorecard, and an unspent holdout
      (any coin but BTC, ETH and SOL).
- [ ] Strategy overlap report (outside review, 2026-11-16): for the pursued strategies,
      daily-return correlation (all days, and on BTC's drawdown days), share of each one's
      return explained by BTC (beta), how each does when BTC is flat or falling. Descriptive,
      no trials; pre-holdout backtests now, paper ledgers as they grow. Expect trend-vol-target
      and btc-regime-trend to be largely one bet; G1 partly.
- [ ] Disclose G1's survivorship: its 8 coins are today's large coins, so its 2018-25
      backtest picks winners in hindsight (its holdout year used a basket fixed by volume
      before 2025-11, so that test is clean). One line in findings and the paper note.
- [ ] Strategies page: re-fetch a note when the browser tab regains focus (notes change
      from the CLI and the page shows a stale copy).

## Later
- [ ] Record Binance's live liquidation and order-book streams on the Pi: forward data,
      untouched by construction, for structural studies later.
- [ ] BNB fees on the venue: kestrel.toml models 7.5 bps (BNB discount), but the Binance
      venue stops on a fee charged in BNB. Value it at BNB's price and keep a small BNB
      balance. Until then testnet fills pay 10 bps and tracking shows fees above the model.
- [ ] Keys from the macOS keychain (or the Pi's key file), not ~/.zshrc: before any live
      (non-testnet) keys exist. Luke adds them himself; Claude never sees the values. Live
      keys: trading only, withdrawals off, restricted to the home IP.
- [ ] News-tone study (`kestrel signal-study news-tone`): needs `kestrel ingest-news
      BTCUSDT ETHUSDT` first (about 40 minutes; GDELT rate-limits), then a note to freeze.
- [ ] Exclude tokenized stocks (AAPLBUSDT...) from the universe before any holdout run.
- [ ] Why G1 works (exploration mode, not trials, not evidence): the BTC filter alone, the
      ranking alone, and risk-adjusted ranking (90-day return / volatility). Round 2 already
      suggested the filter does most of the work. Nothing found here can change G1's rule.
- [ ] Expectancy by conviction bucket (P2 leftover), once a strategy varies its sizing.
- [ ] Dashboard at phone width: the top menu and pages spill off the screen.

## Done recently
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
