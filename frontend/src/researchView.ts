// The Research page's logic, kept out of the component so it can be tested directly.
import type { Decision, LuckPoint, Study } from "./api";

/** A kind of strategy, and which strategy keys belong to it. Descriptions say what the
 * family is, never how it did: results come from research/decisions.toml. */
export interface Family {
  key: string;
  name: string;
  what: string;
  strategies: string[];
}

export const FAMILIES: Family[] = [
  {
    key: "trend",
    name: "Trend following",
    what: "Hold while prices are rising over weeks to months; step aside when they aren't.",
    strategies: [
      "trend-vol-target",
      "trend-trio",
      "trend-basket",
      "trend-vol-target-brake",
      "trend-vol-target-regime",
      "trend-vol-target-stablecoin",
      "trend-vol-target-regime-4h",
      "fx:trend",
    ],
  },
  {
    key: "momentum",
    name: "Momentum and rotation",
    what: "Hold whatever has risen most lately, or rotate between coins by recent strength.",
    strategies: [
      "weekly-momentum",
      "weekly-momentum-brake",
      "btc-led-weekly",
      "xs-momentum",
      "high-anchor",
      "monthly-rotation",
      "rotation-stablecoins",
      "btc-eth-rotation",
      "btc-gold-rotation",
      "eth-btc-value",
    ],
  },
  {
    key: "breakout",
    name: "Breakouts",
    what: "Buy when price leaves a range, a channel or a quiet spell.",
    strategies: ["channel-breakout", "squeeze-breakout", "basket-breakout"],
  },
  {
    key: "reversal",
    name: "Reversals and dips",
    what: "Buy after a fall, in fear, or on a pullback, expecting a bounce.",
    strategies: [
      "drop-reversal",
      "fear-greed",
      "rsi-dip",
      "bollinger-pullback",
      "pivot-reversal",
      "structure-pullback",
      "monday-pullback",
    ],
  },
  {
    key: "pattern",
    name: "Chart patterns and levels",
    what: "Trendlines, wedges, retests, zones and candle patterns, made mechanical.",
    strategies: [
      "level-retest",
      "trendline-bounce",
      "trendline-breakout",
      "tori-trendline",
      "wedge-breakout",
      "pattern-macd",
      "set-and-forget",
    ],
  },
  {
    key: "intraday",
    name: "Minute-chart methods",
    what: "Setups on 1-5 minute bars, where costs are a large share of every trade.",
    strategies: ["order-flow", "choch-fvg"],
  },
  {
    key: "signals",
    name: "Signals from other data",
    what: "Funding rates, stablecoin supply, volatility regimes and the calendar.",
    strategies: ["funding-hold", "funding-trend", "stablecoin-hold", "volatile-macd", "turn-of-month"],
  },
  {
    key: "carry",
    name: "Carry",
    what: "Hold the higher-yielding side and collect the interest difference.",
    strategies: ["fx:carry"],
  },
  {
    key: "holding",
    name: "Risk-managed holding",
    what: "Always in the market, sized down when it is volatile.",
    strategies: ["vol-hold"],
  },
  {
    key: "exploration",
    name: "Exploration graduates",
    what: "Rules found by searching half the altcoins, then run once on the unsearched half.",
    strategies: ["btc-regime-trend", "crash-rebound", "btc-rotation-8", "btc-rotation-4", "altcoin-rotation"],
  },
  {
    key: "shorting",
    name: "Hypothetical shorting",
    what: "Measured outside the engine, which can't short; a pass is needs-shorting, never traded.",
    strategies: ["study:cash-and-carry"],
  },
];

const OTHER: Family = { key: "other", name: "Other", what: "Not yet given a family.", strategies: [] };

export function familyOf(strategy: string): Family {
  return FAMILIES.find((f) => f.strategies.includes(strategy)) ?? OTHER;
}

export interface FamilyRow {
  family: Family;
  decisions: Decision[];
  pursue: number;
  watch: number;
  needsShorting: number;
  drop: number;
}

/** Decisions grouped by family, in FAMILIES order; "Other" last, and only if needed. A
 * family with no decisions yet is left out. */
export function byFamily(decisions: Decision[]): FamilyRow[] {
  const rows = [...FAMILIES, OTHER].map((family) => {
    const own = decisions.filter((d) => familyOf(d.strategy).key === family.key);
    const count = (k: Decision["decision"]) => own.filter((d) => d.decision === k).length;
    return {
      family,
      decisions: own,
      pursue: count("pursue"),
      watch: count("watch"),
      needsShorting: count("needs-shorting"),
      drop: count("drop"),
    };
  });
  return rows.filter((r) => r.decisions.length > 0);
}

/** "1 pursue · 2 watch · 5 drop", leaving out zeros. */
export function tally(row: { pursue: number; watch: number; needsShorting?: number; drop: number }): string {
  const parts: string[] = [];
  if (row.pursue) parts.push(`${row.pursue} pursue`);
  if (row.watch) parts.push(`${row.watch} watch`);
  if (row.needsShorting) parts.push(`${row.needsShorting} needs shorting`);
  if (row.drop) parts.push(`${row.drop} drop`);
  return parts.join(" · ");
}

/** Chart points: one per moment a trial was logged, in seconds. Points logged in the same
 * second keep the last (the chart needs strictly increasing times). */
export function luckSeries(points: LuckPoint[]): { time: number; value: number; trials: number }[] {
  const out: { time: number; value: number; trials: number }[] = [];
  for (const p of points) {
    const time = Math.floor(Date.parse(p.at) / 1000);
    const point = { time, value: p.luck_bar, trials: p.trials };
    if (out.length && out[out.length - 1]!.time === time) out[out.length - 1] = point;
    else out.push(point);
  }
  return out;
}

/** The hover readout above the luck-bar chart. */
export function luckReadout(point: { time: number; value: number; trials: number }): string {
  const day = new Date(point.time * 1000).toISOString().slice(0, 10);
  return `${day}: ${point.trials.toLocaleString("en-AU")} trials, luck bar ${point.value.toFixed(2)}`;
}

/** One coin in a confirmation run. */
export interface ConfirmationCoin {
  symbol: string;
  sharpe: number | null;
  heldSharpe: number | null;
  windows: number;
  windowsBeating: number;
  /** The strategy's Sharpe less holding's; null when either is unknown. */
  gap: number | null;
}

/** A graduate's confirmation run, read from its saved study. */
export interface ConfirmationRun {
  id: string;
  note: string;
  strategy: string;
  verdict: string;
  coins: number;
  beatsShare: number;
  windows: number;
  windowsShare: number;
  medianSharpe: number | null;
  perCoin: ConfirmationCoin[];
}

const num = (x: unknown): number | null => (typeof x === "number" && Number.isFinite(x) ? x : null);

/** Every confirmation run among saved studies, oldest first, its coins sorted from most
 * ahead of holding to most behind (unknown gaps last). */
export function confirmationRuns(studies: Study[]): ConfirmationRun[] {
  return studies
    .filter((s) => s.kind === "confirmation")
    .map((s) => {
      const d = s.detail ?? {};
      const rows = Array.isArray(d.per_coin) ? (d.per_coin as Record<string, unknown>[]) : [];
      const perCoin = rows
        .map((r) => {
          const sharpe = num(r.sharpe);
          const heldSharpe = num(r.held_sharpe);
          return {
            symbol: String(r.symbol),
            sharpe,
            heldSharpe,
            windows: num(r.windows) ?? 0,
            windowsBeating: num(r.windows_beating) ?? 0,
            gap: sharpe === null || heldSharpe === null ? null : sharpe - heldSharpe,
          };
        })
        .sort((a, b) => (b.gap ?? -Infinity) - (a.gap ?? -Infinity));
      return {
        id: s.id,
        note: String(d.note ?? ""),
        strategy: s.strategy,
        verdict: s.verdict,
        coins: num(d.coins) ?? perCoin.length,
        beatsShare: num(d.beats_share) ?? 0,
        windows: num(d.windows) ?? 0,
        windowsShare: num(d.windows_share) ?? 0,
        medianSharpe: num(d.median_sharpe),
        perCoin,
      };
    })
    .sort((a, b) => a.id.localeCompare(b.id));
}
