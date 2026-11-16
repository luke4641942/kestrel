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

/** The research account size (CLAUDE.md): what a study's growth is shown on. */
export const STUDY_CASH = 330;

/** A curve's money view on ``cash``: net profit after costs, total and yearly return, and
 * the worst drawdown. Null without a curve. */
export function curveMoney(
  curve: ShortStudy["curves"][number] | undefined,
  cash = STUDY_CASH,
): { profit: number; total: number; perYear: number; worst: number; first: number; last: number } | null {
  const s = curve?.series;
  if (!s || s.equity.length < 2) return null;
  const growth = s.equity.at(-1)! / s.equity[0]!;
  const first = s.time[0]!;
  const last = s.time.at(-1)!;
  const years = (last - first) / (365 * 86_400);
  return {
    profit: cash * (growth - 1),
    total: growth - 1,
    perYear: years > 0 ? growth ** (1 / years) - 1 : 0,
    worst: Math.min(...s.drawdown),
    first,
    last,
  };
}
