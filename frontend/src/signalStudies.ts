// Plain logic behind the signal studies on the Backtests page, kept out of components so it
// can be tested directly.
import type { SignalPart, SignalSide } from "./api";
import type { Tone } from "./components/ui";

/** A log return as a simple percentage, signed. */
export const weekPct = (x: number | null) => (x === null ? "n/a" : `${x >= 0 ? "+" : "−"}${Math.abs(Math.expm1(x) * 100).toFixed(2)}%`);

/** How a study's verdict reads, and why it fell where it did. */
export function signalVerdict(s: SignalPart): { tone: Tone; label: string; text: string } {
  const w = s.weeks;
  const p = w.p_positive === null ? "n/a" : `${Math.round(w.p_positive * 100)}%`;
  if (s.verdict === "PROCEED") {
    return {
      tone: "ok",
      label: "Proceed",
      text: `The weeks after the signal beat the rest by ${weekPct(w.spread)} (P ${p}), more than costs${
        s.control_holds ? ", after rising and falling weeks alike" : ""
      }. A study, not a strategy: next is a strategy note on the full scorecard.`,
    };
  }
  const reasons: string[] = [];
  if (w.p_positive === null || w.p_positive < 0.9) reasons.push(`P ${p} is below the 90% needed`);
  if (w.spread !== null && w.spread <= s.hurdle) reasons.push(`the spread ${weekPct(w.spread)} doesn't beat one round trip of costs`);
  if (s.control_holds === false) reasons.push("it isn't positive after both rising and falling weeks, so it is momentum or dip-buying in disguise");
  return { tone: "bad", label: "Kill", text: `${reasons.length ? `${capital(reasons.join("; "))}.` : "Doesn't pick better weeks."} No strategy.` };
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** One side's spread for the control rows: on/off week counts and the spread. */
export function sideText(s: SignalSide): string {
  return `${weekPct(s.spread)} (${s.n_on} on, ${s.n_off} off)`;
}
