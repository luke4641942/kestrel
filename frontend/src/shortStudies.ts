// Plain logic behind the hypothetical shorting studies on the Backtests page, kept out of
// components so it can be tested directly.
import type { ShortStudy } from "./api";
import type { Tone } from "./components/ui";

const TITLES: Record<string, string> = {
  "btc-regime-trend": "BTC regime rule with a mirror short",
  "btc-rotation": "G1 with a short side",
  "trend-vol-target": "Trend-vol-target long and short",
  "long-short-momentum": "Long/short momentum among liquid coins",
  "cash-and-carry": "Cash-and-carry on BTC, ETH and SOL perps",
};

export const studyName = (s: { name: string }) => TITLES[s.name] ?? s.name;

/** How a study's verdict reads: a pass can't be traded, so it says so every time. */
export function shortVerdict(s: ShortStudy): { tone: Tone; label: string; text: string } {
  const tests = Object.values(s.tests);
  const passed = tests.filter(Boolean).length;
  if (s.verdict === "NEEDS-SHORTING") {
    return {
      tone: "ok",
      label: "Needs shorting",
      text: `Passed all ${tests.length} of its pre-registered tests. Hypothetical: it needs a short, which Kestrel can't place, so it isn't traded.`,
    };
  }
  return { tone: "bad", label: "Failed", text: `Passed ${passed} of ${tests.length} of its pre-registered tests.` };
}
