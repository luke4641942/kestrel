// Plain logic behind the Charts page: which markets and intervals to offer, how many bars
// a range needs, and remembering the last choice. Kept out of components so it can be
// tested directly.
import type { Dataset, Interval } from "./api";

export const MAIN_MARKETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
/** The API's cap on bars per request. */
export const MAX_BARS = 20_000;

const BARS_PER_DAY: Partial<Record<Interval, number>> = { "1h": 24, "4h": 6, "1d": 1 };
const INTERVAL_ORDER: Interval[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];

export const RANGES: { days: number | null; label: string }[] = [
  { days: 91, label: "3 months" },
  { days: 365, label: "1 year" },
  { days: 3 * 365, label: "3 years" },
  { days: null, label: "Everything stored" },
];

/** Every stored market, the main ones first and the rest alphabetically. */
export function marketOptions(datasets: Dataset[]): { main: string[]; rest: string[] } {
  const symbols = [...new Set(datasets.map((d) => d.symbol))];
  return {
    main: MAIN_MARKETS.filter((m) => symbols.includes(m)),
    rest: symbols.filter((s) => !MAIN_MARKETS.includes(s)).sort(),
  };
}

/** The intervals stored for a market, shortest first. */
export function intervalsFor(datasets: Dataset[], symbol: string): Interval[] {
  const have = new Set(datasets.filter((d) => d.symbol === symbol).map((d) => d.interval));
  return INTERVAL_ORDER.filter((i) => have.has(i));
}

/** Bars needed to show ``days`` of an interval, within the API's cap. */
export function barsFor(interval: Interval, days: number | null): number {
  if (days === null) return MAX_BARS;
  const perDay = BARS_PER_DAY[interval] ?? 24 * 60;
  return Math.min(MAX_BARS, Math.ceil(days * perDay));
}

export interface ChartChoice {
  symbol: string;
  interval: Interval;
  days: number | null;
}

const KEY = "kestrel.charts";

/** The last market, interval and range viewed (per browser), checked against what's stored. */
export function loadChoice(datasets: Dataset[]): ChartChoice | undefined {
  const fallback = (): ChartChoice | undefined => {
    const { main, rest } = marketOptions(datasets);
    const symbol = main[0] ?? rest[0];
    if (!symbol) return undefined;
    const intervals = intervalsFor(datasets, symbol);
    return { symbol, interval: intervals.includes("1d") ? "1d" : intervals[0]!, days: 365 };
  };
  try {
    const saved = JSON.parse(window.localStorage.getItem(KEY) ?? "null") as Partial<ChartChoice> | null;
    if (saved && typeof saved.symbol === "string" && saved.interval) {
      if (intervalsFor(datasets, saved.symbol).includes(saved.interval)) {
        const days = RANGES.some((r) => r.days === saved.days) ? (saved.days ?? null) : 365;
        return { symbol: saved.symbol, interval: saved.interval, days };
      }
    }
  } catch {
    // Unreadable storage: start from the default.
  }
  return fallback();
}

export function saveChoice(choice: ChartChoice): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(choice));
  } catch {
    // Storage blocked: the choice just isn't remembered.
  }
}

/** Switch market, keeping the interval if the new market has it (else daily, else the first). */
export function switchMarket(datasets: Dataset[], choice: ChartChoice, symbol: string): ChartChoice {
  const intervals = intervalsFor(datasets, symbol);
  const interval = intervals.includes(choice.interval)
    ? choice.interval
    : intervals.includes("1d")
      ? "1d"
      : intervals[0]!;
  return { ...choice, symbol, interval };
}
