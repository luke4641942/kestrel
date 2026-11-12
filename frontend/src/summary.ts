// Plain logic behind the Backtests page: the verdict, the test-period wording and the
// chart's trade markers. Kept out of components so it can be tested directly.
import type { Dataset, ResearchStatus, RunDetail, RunSummary, StrategyInfo, StrategyKind } from "./api";
import type { Tone } from "./components/ui";
import type { TradeMarker } from "./components/EquityChart";

export const usdt = (x: number, digits = 2) =>
  x.toLocaleString("en-AU", { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** Signed money: "+1.63", "−440.08" (a true minus sign, not a hyphen). */
export const money = (x: number) => `${x >= 0 ? "+" : "−"}${usdt(Math.abs(x))}`;

/** How each kind of strategy is named in lists. A control is the plainer strategy a
 * research idea has to beat, so it is called a comparison. */
export const KIND_LABEL: Record<StrategyKind, string> = {
  benchmark: "benchmark",
  "engine-test": "engine test",
  research: "research",
  control: "comparison",
};

/** The dataset a form starts on: 4h, then daily, the horizons Kestrel trades (fees
 * dominate anything faster at this account size), else whatever exists. */
export function defaultDataset<T extends { interval: string }>(datasets: T[]): T | undefined {
  return datasets.find((d) => d.interval === "4h") ?? datasets.find((d) => d.interval === "1d") ?? datasets[0];
}

/** What a signals chart shows, when the range had more bars than are drawn. */
export function signalsCaption(start: string, end: string, drawn: number, total: number, firstTime?: number): string {
  if (drawn >= total || firstTime === undefined) return `${start} to ${end}. Research data stops before the holdout.`;
  const from = new Date(firstTime * 1000).toISOString().slice(0, 10);
  return `Signals computed on ${start} to ${end}; the most recent ${drawn.toLocaleString("en-AU")} of ${total.toLocaleString(
    "en-AU",
  )} bars are drawn (from ${from}). Research data stops before the holdout.`;
}

/** Where a research idea stands, in the order it moves through. */
export const RESEARCH_STATUS: Record<ResearchStatus, { tone: Tone; label: string }> = {
  idea: { tone: "neutral", label: "Idea" },
  specified: { tone: "info", label: "Frozen" },
  backtested: { tone: "info", label: "Backtested" },
  validated: { tone: "ok", label: "Validated" },
  paper: { tone: "ok", label: "Paper" },
  live: { tone: "ok", label: "Live" },
  rejected: { tone: "bad", label: "Rejected" },
};

export type VerdictTone = "ok" | "warn" | "bad" | "neutral";

export interface Verdict {
  tone: VerdictTone;
  headline: string;
  /** Whether the result can be trusted yet. */
  trust: string;
  profit: number;
  btcProfit: number;
  vsBtc: number;
  isBenchmark: boolean;
  trades: number;
  wins: number;
}

/** What the benchmark holds: the symbol without its quote currency (ETHUSDT -> ETH).
 * Every run is compared with buy-and-hold of its own market, not always BTC. */
export function heldAsset(r: { symbol: string; account_currency: string; benchmark_symbol?: string | null }): string {
  const cur = r.account_currency;
  const held = r.benchmark_symbol ?? r.symbol;
  return cur && held.endsWith(cur) ? held.slice(0, -cur.length) : held;
}

/** The market a cross-sectional strategy runs on: every coin in the stored universe. */
export const UNIVERSE = "UNIVERSE";

/** "All coins 1d" for a cross-sectional run, else "BTCUSDT 4h". */
export function marketLabel(r: { symbol: string; interval: string; universe_coins?: number | null }): string {
  if (r.symbol !== UNIVERSE) return `${r.symbol} ${r.interval}`;
  return `All coins${r.universe_coins ? ` (${r.universe_coins})` : ""} ${r.interval}`;
}

/** The markets a strategy can run on. A cross-sectional strategy runs on the whole
 * universe of daily bars, dated by BTC's daily history (its benchmark); one-market
 * strategies run on any stored dataset. */
export function marketsFor(impl: Pick<StrategyInfo, "universe"> | undefined, datasets: Dataset[]): Dataset[] {
  if (!impl?.universe) return datasets;
  const btc = datasets.find((d) => d.symbol === "BTCUSDT" && d.interval === "1d");
  return btc ? [{ ...btc, symbol: UNIVERSE }] : [];
}

/** Did it make money, did it beat holding the same market, and can it be trusted? */
export function verdict(r: RunDetail): Verdict {
  const m = r.metrics;
  const start = Number(r.initial_cash);
  const profit = m.final_equity - start;
  const btcProfit = r.benchmark_metrics.final_equity - start;
  const vsBtc = profit - btcProfit;
  const isBenchmark = r.strategy === "buy-and-hold";
  const cur = r.account_currency;
  const asset = heldAsset(r);
  const trades = r.style === "brackets" ? (m.round_trips ?? 0) : m.trades;
  const wins = r.trades.filter((t) => t.r_multiple > 0).length;

  let tone: VerdictTone;
  let headline: string;
  if (isBenchmark) {
    tone = "neutral";
    headline = `The benchmark: simply holding ${asset} ${profit >= 0 ? "made" : "lost"} ${usdt(Math.abs(profit))} ${cur}.`;
  } else if (profit <= 0) {
    tone = "bad";
    headline = `Lost money: ${usdt(Math.abs(profit))} ${cur} down after costs.`;
  } else if (vsBtc < 0) {
    tone = "warn";
    headline = `Made money, but less than just holding ${asset} (${usdt(btcProfit)} ${cur}).`;
  } else {
    tone = "ok";
    headline = `Beat holding ${asset} by ${usdt(vsBtc)} ${cur} after costs.`;
  }

  let trust: string;
  if (isBenchmark) {
    // One buy is the whole strategy, so "too few trades" doesn't apply.
    trust = `The bar every strategy on ${asset} has to clear, after the same costs on the same bars.`;
  } else if (m.few_trades) {
    trust = `Only ${trades} trade${trades === 1 ? "" : "s"}: too few to tell skill from luck. Aim for 30 or more.`;
  } else if (m.psr === null) {
    trust = "Equity never moved, so there's nothing to measure.";
  } else {
    // PSR is the confidence that the true Sharpe is above zero, from this one sample;
    // it is not corrected for the number of variants tried (that is P3's deflated Sharpe).
    const conf = `${Math.round(m.psr * 100)}% confidence the true Sharpe is above zero`;
    if (m.psr >= 0.95) trust = `Statistically convincing: ${conf}, before correcting for variants tried.`;
    else if (m.psr >= 0.8) trust = `Promising but not proven: ${conf}.`;
    else trust = `Not convincing: only ${conf}. This could easily be luck.`;
    trust += deflatedNote(r);
  }
  return { tone, headline, trust, profit, btcProfit, vsBtc, isBenchmark, trades, wins };
}

/** "3 years 10 months", "7 months", "12 days". */
export function span(startIso: string, endIso: string): string {
  const days = (new Date(endIso).getTime() - new Date(startIso).getTime()) / 86_400_000;
  if (days < 60) return `${Math.round(days)} days`;
  let years = Math.floor(days / 365.25);
  let months = Math.round((days - years * 365.25) / 30.44);
  if (months === 12) {
    years += 1;
    months = 0;
  }
  const parts = [years && `${years} year${years === 1 ? "" : "s"}`, months && `${months} month${months === 1 ? "" : "s"}`];
  return parts.filter(Boolean).join(" ");
}

/** How long paper trading must run before a Sharpe like this one is credible. */
export function trackRecord(days: number | null): string {
  if (days === null) return "Never, at this Sharpe";
  const years = days / 365;
  if (years > 50) return "More than 50 years";
  if (years >= 1) return `About ${years.toFixed(1)} years`;
  const months = Math.max(1, Math.round(days / 30.44));
  return `About ${months} month${months === 1 ? "" : "s"}`;
}

/** Whether costs could rise before the edge disappears, in plain words. */
export function costHeadroom(breakEven: number | null, paying: number | null): string {
  if (breakEven === null || paying === null) return "No trades, so no costs to compare.";
  if (breakEven <= paying) return "No room: costs already exceed the edge.";
  const ratio = breakEven / Math.max(paying, 1e-9);
  return ratio >= 3 ? `Robust: costs could rise ${ratio.toFixed(0)}x before the edge is gone.` : `Fragile: costs could only rise ${ratio.toFixed(1)}x before the edge is gone.`;
}

/** Labels beyond this many trades overlap into noise; colour still shows each result. */
export const MAX_LABELLED_TRADES = 20;

/** Beyond this many markers the arrows cover the equity line; the trade list has them all. */
export const MAX_MARKED_TRADES = 150;

/** Exits coloured (and, when few, labelled) by result for bracket runs; buys and sells
 * for weight runs. */
export function tradeMarkers(r: RunDetail): TradeMarker[] {
  const epoch = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);
  if (tooManyToMark(r)) return [];
  if (r.style === "brackets") {
    const labelled = r.trades.length <= MAX_LABELLED_TRADES;
    return r.trades.flatMap((t): TradeMarker[] => [
      { time: epoch(t.entry_time), side: "buy" },
      {
        time: epoch(t.exit_time),
        side: "sell",
        good: t.r_multiple > 0,
        label: labelled ? `${t.r_multiple >= 0 ? "+" : ""}${t.r_multiple.toFixed(1)}R` : undefined,
      },
    ]);
  }
  return r.fills.map((f) => ({ time: epoch(f.time), side: f.side }));
}

export function tooManyToMark(r: RunDetail): boolean {
  return (r.style === "brackets" ? r.trades.length : r.fills.length / 2) > MAX_MARKED_TRADES;
}

/** What a run was: a logged trial of a research note, or which kind of strategy. */
export function runRole(r: Pick<RunSummary, "research" | "strategy_kind">): string {
  return r.research ? `trial of ${r.research.slug} v${r.research.version}` : KIND_LABEL[r.strategy_kind];
}

/** "swing_bars=3 target_r=4", or "default parameters" when there are none. */
export function paramsText(params: Record<string, number>): string {
  const entries = Object.entries(params);
  return entries.length ? entries.map(([k, v]) => `${k}=${v}`).join(" ") : "no parameters";
}

/** Trades for the summary: round trips for bracket runs, fills (orders) otherwise. */
export function tradeCount(r: Pick<RunSummary, "style" | "metrics">): number {
  return r.style === "brackets" ? (r.metrics.round_trips ?? 0) : r.metrics.trades;
}

/** Too few trades to trust per-trade statistics. Never said of the benchmark, whose one
 * buy is the whole strategy. */
export function fewTrades(r: Pick<RunSummary, "strategy" | "metrics">): boolean {
  return r.metrics.few_trades && r.strategy !== "buy-and-hold";
}

export type RunSortKey = "profit" | "vsHold" | "sharpe" | "drawdown" | "trades" | "saved";

export interface RunSort {
  key: RunSortKey;
  dir: "desc" | "asc";
}

export const DEFAULT_RUN_SORT: RunSort = { key: "saved", dir: "desc" };

/** The number a runs-table column sorts by; null when the run has none. Profit sorts by
 * return, not cash, so runs with different starting cash compare fairly. */
function sortValue(r: RunSummary, key: RunSortKey): number | null {
  const m = r.metrics;
  switch (key) {
    case "profit":
      return m.total_return;
    case "vsHold":
      // The benchmark against itself is not a result.
      return r.strategy === "buy-and-hold" ? null : m.total_return - r.benchmark_metrics.total_return;
    case "sharpe":
      return m.sharpe;
    case "drawdown":
      return m.max_drawdown; // negative: the shallowest drawdown is the largest value
    case "trades":
      return tradeCount(r);
    case "saved":
      return new Date(r.created_at).getTime();
  }
}

/** Sorted copy of the runs. Descending puts the best first in every column; runs with no
 * value go last either way. Ties keep their existing order. */
export function sortRuns(runs: RunSummary[], sort: RunSort): RunSummary[] {
  const sign = sort.dir === "desc" ? -1 : 1;
  return [...runs].sort((a, b) => {
    const x = sortValue(a, sort.key);
    const y = sortValue(b, sort.key);
    if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
    return sign * (x - y);
  });
}

/** Clicking a column: a new column sorts best first; the same column flips direction. */
export function nextSort(current: RunSort, key: RunSortKey): RunSort {
  return current.key === key ? { key, dir: current.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" };
}

/** The universe spread bands in words: "1 bp over 500M a day, 3 bps over 100M a day, …, else 15 bps". */
export function spreadBands(tiers: [number, number][]): string {
  const bps = (b: number) => `${b} bp${b === 1 ? "" : "s"}`;
  const short = (v: number) => (v >= 1e9 ? `${v / 1e9}B` : `${v / 1e6}M`);
  return tiers.map(([v, b]) => (v > 0 ? `${bps(b)} over ${short(v)} a day` : `else ${bps(b)}`)).join(", ");
}

/** " After 31 strategies tried, 4% chance it beats the best of them by luck." */
export function deflatedNote(r: Pick<RunSummary, "deflated_sharpe" | "deflated_trials">): string {
  if (r.deflated_sharpe == null || !r.deflated_trials || r.deflated_trials < 2) return "";
  return ` After ${r.deflated_trials} strategies tried: ${Math.round(r.deflated_sharpe * 100)}% chance it beats the best of them by luck.`;
}
