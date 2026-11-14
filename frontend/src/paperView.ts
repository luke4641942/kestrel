// Plain logic behind the Trading page: how a paper account is tracking its model, in one
// line. Kept out of components so it can be tested directly.
import type { PaperDetail } from "./api";
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
