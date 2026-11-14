// Plain logic behind the Regimes page: state names, chart shading and the latest reading.
// Kept out of components so it can be tested directly.
import type { RegimeDetail } from "./api";

/** One colour per state, calmest to wildest: a single hue from light to dark, because the
 * states are ordered by volatility (a magnitude), not unrelated categories. */
export const REGIME_COLORS = ["--regime-1", "--regime-2", "--regime-3"] as const;

/** States are sorted by volatility after every fit, so names follow from the position. */
export function stateName(state: number, states: number): string {
  if (states === 2) return ["Calm", "Volatile"][state] ?? `State ${state + 1}`;
  if (states === 3) return ["Calm", "Normal", "Volatile"][state] ?? `State ${state + 1}`;
  return `State ${state + 1}`;
}

export function stateColor(state: number): string {
  return REGIME_COLORS[Math.min(state, REGIME_COLORS.length - 1)]!;
}

/** Days with a state, for shading behind the price: one entry per fitted day. */
export function shading(d: RegimeDetail): { time: number; state: number }[] {
  const out: { time: number; state: number }[] = [];
  d.state.forEach((s, i) => {
    if (s !== null) out.push({ time: d.time[i]!, state: s });
  });
  return out;
}

/** Shading strength per state: calm days barely tinted, so the restless spells stand out. */
export const SHADE_ALPHA = [0.06, 0.22, 0.42] as const;

export function shadeAlpha(state: number): number {
  return SHADE_ALPHA[Math.min(state, SHADE_ALPHA.length - 1)]!;
}

/** The first day with a state: fits need four years of history, so states start later than
 * the data does. */
export function firstFitted(d: RegimeDetail): number | null {
  const i = d.state.findIndex((s) => s !== null);
  return i < 0 ? null : d.time[i]!;
}

/** The last fitted day: which state the model believed, and how sure it was. */
export function latestReading(d: RegimeDetail): { time: number; state: number; probability: number } | null {
  for (let i = d.state.length - 1; i >= 0; i--) {
    const s = d.state[i];
    const p = d.probabilities[i];
    if (s !== null && s !== undefined && p) return { time: d.time[i]!, state: s, probability: p[s]! };
  }
  return null;
}

/** Each state's probability as a line, with nulls (before the first fit) left out. */
export function probabilityLines(d: RegimeDetail): { time: number; value: number }[][] {
  const states = d.states.length;
  return Array.from({ length: states }, (_, k) =>
    d.probabilities.flatMap((p, i) => (p ? [{ time: d.time[i]!, value: p[k]! }] : [])),
  );
}
