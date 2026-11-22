// The guided tour of the dashboard: its stops as data, and the plain logic around them (which
// run to open, where the card goes). The component (components/Tour.tsx) only draws them.
// The stops follow how a result is judged, and one stop shows a strategy failing its tests
// on purpose: what sets Kestrel apart is that it doesn't fool itself.
import type { RunSummary } from "./api";

export interface TourStep {
  /** The part of the story this stop belongs to, shown above its title. */
  chapter: string;
  /** Where the stop is: a hash route ("rules", "strategies/<slug>"), or "run" for the run
   * chosen by pickRun from the saved backtests. */
  route: string;
  /** The element to highlight: its data-tour name, or a heading id. */
  target: string;
  title: string;
  body: string;
}

export const TOUR: TourStep[] = [
  {
    chapter: "The question",
    route: "thesis",
    target: "thesis",
    title: "The question",
    body: "Kestrel asks one thing honestly: does any rule beat simply holding the coin, after costs, once you count how many rules were tried? This page is the answer so far: not yet proven. The tour shows how that's judged.",
  },
  {
    chapter: "How it's judged",
    route: "rules",
    target: "pass",
    title: "What counts as a pass",
    body: "A strategy in cash half the time halves its drawdown for free. So it must beat the same coin held at the same average exposure, on Sharpe, after costs, and then clear the luck bar set by every trial so far.",
  },
  {
    chapter: "How it's judged",
    route: "rules",
    target: "scorecard",
    title: "One scorecard for everything",
    body: "Nine tests, the same for every strategy: edge, luck, walk-forward, start dates, cost headroom, trade count, drawdown, neighbouring settings, and random entries for bracket strategies. A research note may add tests, never remove one.",
  },
  {
    chapter: "How it's judged",
    route: "strategies/btc-regime-trend-on-eth-and-sol",
    target: "note",
    title: "Written down before it's run",
    body: "Every idea is a research note: hypothesis, exact rules, who pays the profit and why, and what would falsify it. It's frozen before it sees data; a change needs a new version, and every run is logged as a trial against it.",
  },
  {
    chapter: "How it's judged",
    route: "run",
    target: "scorecard",
    title: "A strategy failing honestly",
    body: "Trend following on BTC, 2020-25, after costs. It beats BTC held at the same exposure (Sharpe 1.21 vs 1.07), yet fails two tests: luck, and walk-forward (2 of 7 windows). It's paper-traded as risk-managed holding, never called an edge.",
  },
  {
    chapter: "The evidence",
    route: "research",
    target: "families",
    title: "Everything tried, by family",
    body: "Each strategy's decision: pursue (paper trading), watch, or drop. Most are dropped: chart patterns, dip-buying, minute-chart setups, altcoin pickers. A dropped idea stays dropped; reviving it means a new note whose runs count.",
  },
  {
    chapter: "The evidence",
    route: "research",
    target: "luck",
    title: "The luck bar rises with every trial",
    body: "The best of many worthless strategies looks good by chance. Every setting tried is counted, grids included, and the deflated Sharpe asks whether a result beats the best that many tries would produce by luck alone.",
  },
  {
    chapter: "The evidence",
    route: "research",
    target: "exploration",
    title: "Explore, then confirm",
    body: "The coins were split once, at random, before anyone looked. One half can be searched freely, and none of it counts. Graduates run once on the locked half under a test fixed in advance. One rule passed; one failed.",
  },
  {
    chapter: "Running it",
    route: "trading",
    target: "accounts",
    title: "Running forward",
    body: "The surviving strategies paper-trade on Binance's testnet through the same code as the backtest. A day-by-day replay must match the backtest exactly, and each account says what it's waiting for before it buys.",
  },
  {
    chapter: "Running it",
    route: "costs",
    target: "order",
    title: "Costs on every fill",
    body: "Every order pays the exchange fee, half the spread and market impact, at the real account size (330 USDT), with Binance's 5 USDT minimum. At this size fees dominate, so nothing is shown without them.",
  },
  {
    chapter: "The limits",
    route: "thesis",
    target: "what-these-results-can-t-tell-you",
    title: "What it can't tell you",
    body: "Costs are modelled until paper fills measure them; 2020-25 is mostly one crypto cycle; the holdout is spent for the major coins. It may be that nothing here is an edge, and that's an acceptable result.",
  },
];

/** The run the tour opens: trend-vol-target on BTC under its own note (daily, 2020-01 to
 * 2025-10, the run quoted in the findings), else the newest trend-vol-target BTC run. */
export function pickRun(runs: RunSummary[]): string | null {
  const tvt = runs
    .filter((r) => r.strategy === "trend-vol-target" && r.symbol === "BTCUSDT")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const main = tvt.find(
    (r) =>
      r.research?.slug === "trend-following-with-volatility-targeting-btc" &&
      r.interval === "1d" &&
      r.start === "2020-01" &&
      r.end === "2025-10",
  );
  return (main ?? tvt[0])?.id ?? null;
}

/** The hash a stop shows: "run" becomes the chosen run, or the Backtests page without one. */
export function stepHash(step: TourStep, run: string | null): string {
  if (step.route !== "run") return `/${step.route}`;
  return run ? `/backtests/${run}` : "/backtests";
}

/** Where a stop's element should sit, from the top of the screen, so it shows in the space
 * between the top bar and the docked card: in the middle of that space, or from its top
 * if it's too tall to fit. */
export function wantedTop(element: number, screen: number, header: number, card: number, gap = 16): number {
  const room = screen - header - card - 2 * gap;
  if (element > room * 0.9) return header + gap;
  return header + gap + (room - element) / 2;
}
