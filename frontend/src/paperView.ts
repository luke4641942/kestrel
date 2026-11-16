// Plain logic behind the Trading page: how a paper account is tracking its model, in one
// line. Kept out of components so it can be tested directly.
import type { PaperDetail, PaperSummary } from "./api";
import type { Tone } from "./components/ui";

/** The verdict that matters first: do decisions match the backtest's code, and how far
 * have fills and equity drifted from the model? A decision mismatch is always a bug. */
export function trackingVerdict(d: PaperDetail): { tone: Tone; text: string } {
  const t = d.tracking;
  if (t.days === 0) return { tone: "neutral", text: "Nothing handled yet: the first decision comes after the next close." };
  if (t.decision_mismatches > 0) {
    return {
      tone: "bad",
      text: `${t.decision_mismatches} of ${t.days} decisions differ from the model. The same code decides both, so this is a bug to find before trusting anything else here.`,
    };
  }
  const parts = [`Decisions match the model on all ${t.days} days.`];
  if (t.difference !== null) {
    const pct = (t.difference * 100).toFixed(2);
    parts.push(`Equity is ${t.difference >= 0 ? "+" : ""}${pct}% against the model.`);
  }
  if (t.cost_bps !== null && t.expected_bps !== null) {
    parts.push(`Fills cost ${t.cost_bps.toFixed(1)} bps each against ${t.expected_bps.toFixed(1)} modelled.`);
  }
  if (t.missed > 0) parts.push(`${t.missed} day${t.missed === 1 ? " was" : "s were"} missed (the run was late).`);
  const drift = Math.abs(t.difference ?? 0) > 0.02 || t.missed > 0;
  return { tone: drift ? "warn" : "ok", text: parts.join(" ") };
}

export function venueLabel(venue: string): string {
  return venue === "testnet" ? "Binance testnet" : venue === "sim" ? "Simulated" : venue;
}

const coin = (symbol: string) => symbol.replace(/USDT$/, "");

/** A basket account trades a fixed set of coins rather than one symbol. */
export const isBasket = (d: { symbol: string }) => d.symbol === "UNIVERSE";

/** What an account holds, as the Holding stat shows it: a value and a note. */
export function holdingView(d: PaperSummary): { value: string; note: string } {
  const cash = `Cash ${d.cash.toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT`;
  if (!isBasket(d)) return { value: `${d.position} ${coin(d.symbol)}`, note: cash };
  const held = Object.entries(d.positions).sort(([a], [b]) => a.localeCompare(b));
  if (held.length === 0) return { value: "Cash only", note: cash };
  return {
    value: `${held.length} coin${held.length === 1 ? "" : "s"}`,
    note: `${held.map(([s, q]) => `${q} ${coin(s)}`).join(", ")}; ${cash.toLowerCase()}`,
  };
}

/** A basket decision's targets in a line: "SOL 17%, DOGE 15%", or "Cash" when it holds
 * nothing (BTC's regime is off). A coin kept as held shows as "kept". */
export function targetsText(targets: Record<string, number | null>): string {
  const parts = Object.entries(targets)
    .sort(([, a], [, b]) => (b ?? 0) - (a ?? 0))
    .map(([s, w]) => (w === null ? `${coin(s)} kept` : `${coin(s)} ${(w * 100).toFixed(0)}%`));
  return parts.length ? parts.join(", ") : "Cash";
}
