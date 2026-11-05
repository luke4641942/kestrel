// Plain logic behind the Backtests page: the verdict, the test-period wording and the
// chart's trade markers. Kept out of components so it can be tested directly.
import type { RunDetail } from "./api";
import type { TradeMarker } from "./components/EquityChart";

export const usdt = (x: number, digits = 2) =>
  x.toLocaleString("en-AU", { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** Signed money: "+1.63", "−440.08" (a true minus sign, not a hyphen). */
export const money = (x: number) => `${x >= 0 ? "+" : "−"}${usdt(Math.abs(x))}`;

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

/** Did it make money, did it beat holding BTC, and can the result be trusted? */
export function verdict(r: RunDetail): Verdict {
  const m = r.metrics;
  const start = Number(r.initial_cash);
  const profit = m.final_equity - start;
  const btcProfit = r.benchmark_metrics.final_equity - start;
  const vsBtc = profit - btcProfit;
  const isBenchmark = r.strategy === "buy-and-hold";
  const cur = r.account_currency;
  const trades = r.style === "brackets" ? (m.round_trips ?? 0) : m.trades;
  const wins = r.trades.filter((t) => t.r_multiple > 0).length;

  let tone: VerdictTone;
  let headline: string;
  if (isBenchmark) {
    tone = "neutral";
    headline = `The benchmark: simply holding BTC ${profit >= 0 ? "made" : "lost"} ${usdt(Math.abs(profit))} ${cur}.`;
  } else if (profit <= 0) {
    tone = "bad";
    headline = `Lost money: ${usdt(Math.abs(profit))} ${cur} down after costs.`;
  } else if (vsBtc < 0) {
    tone = "warn";
    headline = `Made money, but less than just holding BTC (${usdt(btcProfit)} ${cur}).`;
  } else {
    tone = "ok";
    headline = `Beat holding BTC by ${usdt(vsBtc)} ${cur} after costs.`;
  }

  let trust: string;
  if (m.few_trades) {
    trust = `Only ${trades} trade${trades === 1 ? "" : "s"}: too few to tell skill from luck. Aim for 30 or more.`;
  } else if (m.psr === null) {
    trust = "Equity never moved, so there's nothing to measure.";
  } else if (m.psr >= 0.95) {
    trust = `Statistically convincing: ${Math.round(m.psr * 100)}% chance the edge is real (before correcting for variants tried).`;
  } else if (m.psr >= 0.8) {
    trust = `Promising but not proven: ${Math.round(m.psr * 100)}% chance the edge is real.`;
  } else {
    trust = `Not convincing: only a ${Math.round(m.psr * 100)}% chance the edge is real. This could easily be luck.`;
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

/** Labels beyond this many trades overlap into noise; colour still shows each result. */
export const MAX_LABELLED_TRADES = 20;

/** Exits coloured (and, when few, labelled) by result for bracket runs; buys and sells
 * for weight runs. */
export function tradeMarkers(r: RunDetail): TradeMarker[] {
  const epoch = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);
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
