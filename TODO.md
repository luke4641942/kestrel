# To-do

The active list. Claude keeps it up to date in the same commit as the work (see CLAUDE.md).
Decisions and rules live in CLAUDE.md; results in `research/findings.md`.

## Waiting on Luke
- [ ] Paper trading is live on the testnet (reloaded from ~/kestrel 2026-11-15): tvt-btc,
      tvt-eth, tvt-sol, hourly at 10 past. First runs OK: BTC bought 33%, ETH and SOL 0%.
      Reviews every 3 months (next around 2027-02-15); failure-only check at 182 days
      (around 2027-05-15); verdict at 395 days (around 2027-12-14).

## Next
- [ ] btc-regime-trend on ETH and SOL: PURSUE (2026-11-16, Claude's call delegated by
      Luke): passes every scorecard test but luck on both. Paper runner ready (2026-11-16:
      refreshes BTC for btc_close, refuses missing inputs, replay matches the backtest).
      Next: a paper-trading note for Luke to freeze, then testnet accounts beside
      trend-vol-target on ETH and SOL.
- [ ] Delistings study: run 2026-11-15, verdict EXIT RULE (coins fell 39.8% on average from
      the day after the announcement to removal; no rebound; findings item 11). Luke's call:
      write an exit-rule note (sell a held coin at the next open after its delisting is
      announced; matters only for strategies that hold small coins, none running now), or
      park it. The hypothetical perp short was INSUFFICIENT (2026-11-16, findings 11).

## Later
- [ ] Keys from the macOS keychain, not ~/.zshrc: parked until a live (non-testnet) account
      (Luke, 2026-11-15); do it before any live keys exist. Luke adds them
      himself (`security add-generic-password -s kestrel-testnet -a key -w`, and `-a secret`);
      Claude never sees the values. Kestrel reads them at run time; the keychain item's access
      list allows only Kestrel's Python (`-T .venv/bin/python`), not `security` in general, so
      any other reader (including a command Claude runs) triggers a macOS prompt Luke can deny.
      Then remove them from ~/.zshrc and stop the launchd job sourcing it.
- [ ] Run the paper trader on Luke's TrueNAS server (Luke, 2026-11-15) so it keeps running
      while the Mac sleeps. Likely a container (TrueNAS app) running `kestrel paper run`
      hourly at 10 past, with the repo, .venv and `data/paper/` on the NAS. Only one machine
      may run an account: two runners would both place orders and write the same ledger, so
      unload the launchd jobs (`kestrel paper schedule <name> --remove`) in the same step.
      Keys from a secrets file or env readable only by the container, not ~/.zshrc. The
      dashboard reads `data/paper/` there (or a synced copy).
- [ ] BNB fees on the venue: kestrel.toml models 7.5 bps (BNB discount) from 2026-11-15, but
      the Binance venue stops on a fee charged in BNB. Account for it (value the BNB fee at
      BNB's price), keep a small BNB balance and top it up. Until then testnet and live fills
      pay 10 bps in the traded assets, and tracking will show fees above the model.
- [ ] News-tone study (Luke's pick over Twitter, 2026-11-14): code built (`kestrel.data.news`,
      `kestrel signal-study news-tone`). Needs `kestrel ingest-news BTCUSDT ETHUSDT` first:
      GDELT allows ~1 request every 5-10s, so the 2017-25 history takes ~40+ minutes (run it in
      a spare terminal; it resumes if stopped). Then a note for Luke to freeze, then the study.
- [ ] BTC's regime turning volatile before ETH's, as an early ETH trigger (Luke's idea,
      2026-11-14). Only 11 such moments on daily regimes 2021-25: too few to test. Revisit
      only if the MACD study finds a direction, with hourly regimes for more events.
- [ ] Exclude tokenized stocks (AAPLBUSDT...) from the universe before any holdout run.
- [ ] Expectancy by conviction bucket (P2 leftover), once a strategy varies its sizing.
- [ ] Dashboard at phone width: the top menu and pages spill off the screen.
- [ ] HMM regime filter (backlog S2) as a strategy: needs a note that passes the entry bar.

## Done recently
- 2026-11-16 Research tab: every decision by family, the luck bar over time (`/api/validation/history`), the findings. Next: talk through the exploration/confirmation rule (Luke).
- 2026-11-16 Trend-vol-target on gold, EUR, GBP: fails on all three; dropped (Luke). Tabs reordered (Luke).
- 2026-11-16 YouTube leftovers tested and dropped (Luke): order flow (ETH, SOL 5m), CHoCH + FVG (SOL 5m NY), Set and Forget (BTC, ETH 1H) all fail test 1. Engine: resting limit orders; spread-band fix for sub-daily bars.
- 2026-11-16 Hypothetical delisting short (perps): INSUFFICIENT; 12 coins in 6 announcements shortable to removal, -7.8% net at 2x, 3 of 12 liquidated.
- 2026-11-15 Delisting study: EXIT RULE (-39.8% from day 1 to removal, no rebound).
- 2026-11-15 Old ~/Downloads/kestrel.zip deleted after checking nothing in it was missing (Luke).
- 2026-11-15 Project moved to ~/kestrel (launchd can't read ~/Downloads); paper jobs rewritten, reloaded and run once.
- 2026-11-15 Paper jobs loaded (Luke). BTC/gold rotation tested and dropped. TODO tidied.
- 2026-11-15 Paper trading opened: tvt-btc, tvt-eth, tvt-sol on the testnet, schedules written. Trend-trio: watch.
- 2026-11-15 Trend-vol-target on ETH and SOL: pursue (Luke), after passing their holdouts.
- 2026-11-15 Trend-vol-target finished on ETH and SOL: both match BTC's standard; holdout notes drafted. Strategies page cleaned (three rotation notes rejected).
- 2026-11-15 Three stablecoin pairings tested: two dropped, the volatility-sized hold folded into the stablecoin watch (Luke).
- 2026-11-15 Stablecoin hold: beats holding on BTC and ETH but fails five tests; watch (Luke). Rotation dropped (Luke), with its stablecoin version.
- 2026-11-15 Cost re-checks at BNB fees: verdicts unchanged. Stablecoin study: PROCEED.
- 2026-11-15 ETH/BTC relative value: fails the scorecard; dropped (Luke).
- 2026-11-15 Strategies page cleaned up (Luke): three notes of dropped strategies rejected; trend walk-forward and rotation holdout notes kept.
- 2026-11-15 Rotation + MACD: 7 of 9 tests (fails drawdown by 0.7 points, luck 57%); watch. BTC/gold rotation: dropped.
- 2026-11-15 Monthly rotation holdout: FAILED (-31.5% vs -6.6% for BTC at its exposure); kept on watch (Luke).
- 2026-11-15 Eight YouTube notes rejected (Luke): monthly rotation on watch, the other seven dropped.
- 2026-11-15 Eight YouTube notes run (22 settings, trials 193 -> 473): every one fails.
- 2026-11-15 Validation additions from batch 2: the max drawdown on 2,000 reshuffled paths
  (median and 1 in 20) on every backtest. Results by year and walk-forward's
  out-of-sample share of in-sample already existed.
- 2026-11-15 Cost gate shown on the dashboard (More > What a trade costs), saved by `kestrel cost-gate`.
- 2026-11-15 Random-entry control built: `kestrel random-entry`, scorecard test 9 for bracket
  strategies (beat 95% of random-entry paths with the same stops, targets and sizing).
- 2026-11-15 Cost gate built and run on BTC/ETH/SOL 1m and 5m (12 months each ingested):
  FAILS 17 of 18, SOL 5m NY open MARGINAL.
- 2026-11-14 Attention and extreme-fear studies: both KILL; rejected (Luke).
- 2026-11-14 P4 paper trading built: sessions S1-S5 (details in CLAUDE.md's roadmap).
- 2026-11-14 volatile-macd backtested: fails the scorecard on BTC and ETH; rejected (Luke).
- 2026-11-14 Volatile-spell direction study: TREND (spread +3.70%/week, P 98%, 75 weeks).
- 2026-11-14 Regime filter on trend-vol-target: failed (Sharpe 0.64 vs 0.69); rejected (Luke).
- 2026-11-14 P4 S5: catch-up, tracking, schedule, Trading page; paper-trading note drafted.
- 2026-11-14 P4 S4: safety layer; a real kill-switch drill on the testnet.
- 2026-11-14 P4 S3: Binance Spot Testnet venue; a real testnet order filled end to end.
- 2026-11-14 P4 S2: paper runner on the simulated venue; replay identical to the backtest.
- 2026-11-14 P4 S1: shared rebalance step (five real runs identical) and `kestrel update`.
- 2026-11-14 Holdout tests shown on the Overview (read-only, `/api/holdout`).
- 2026-11-14 Labels (Luke): trend-vol-target BTC pursue; weekly momentum ETH drop.
- 2026-11-14 Holdout used once: trend-vol-target BTC passed, weekly momentum ETH failed.
- 2026-11-14 `kestrel holdout-test` built (synthetic tests only; the real holdout is untouched).
- 2026-11-14 Trend-vol-target BTC moved from pursue to watch (Luke). Nothing is on pursue.
- 2026-11-14 Trend-vol-target BTC walk-forward, window by window: beat holding in 2 of 7; fails test 3.
- 2026-11-14 Charts page under More, with a plain-English card per indicator.
- 2026-11-14 Crash brake tested on both survivors: made both worse; rejected.
- 2026-11-14 Weekly momentum ETH scorecard: passes all but luck (walk-forward 4 of 7); stays watch.
- 2026-11-14 Scorecard for strategies with nothing to tune (fixed-rule walk-forward, robustness n/a).
- 2026-11-14 Strategies cleaned out: seven tested notes rejected with their reasons; empty "Test" idea removed.
- 2026-11-14 Weekly momentum ETH moved to "watch"; BTC and SOL stay dropped.
- 2026-11-14 Overview: the datasets line no longer lists every coin.
- 2026-11-14 Regimes page under More (HMM states, fitted as a job).
- 2026-11-14 HMM regime engine (`kestrel.regimes`, `kestrel regimes`).
- 2026-11-14 Scorecard graded on every backtest.
- 2026-11-14 Findings write-up for employers (`research/findings.md`).
- 2026-11-14 Rules reviewed; Rules page under More; `scripts/check.sh`.
