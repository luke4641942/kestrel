// Plain logic behind the event studies on the Backtests page, kept out of components so it
// can be tested directly.
import type { EventStudy } from "./api";
import type { Tone } from "./components/ui";

const TITLES: Record<string, string> = {
  "perp-listing": "A coin's first perp listing",
  "token-unlocks": "Large token unlocks",
};

export const eventName = (s: { name: string }) => TITLES[s.name] ?? s.name;

/** How a study's verdict reads. An exit rule is a reason not to hold a coin, not a trade. */
export function eventVerdict(s: EventStudy): { tone: Tone; label: string; text: string } {
  const tests = Object.values(s.tests);
  const passed = tests.filter(Boolean).length;
  if (s.verdict === "EXIT RULE") {
    return {
      tone: "ok",
      label: "Exit rule",
      text: `Passed all ${tests.length} of its pre-registered tests: a reason not to hold a coin around this event (a rule for an altcoin portfolio), not a trade by itself.`,
    };
  }
  if (s.verdict === "INSUFFICIENT") {
    return { tone: "warn", label: "Insufficient", text: "Too few events to judge." };
  }
  return { tone: "bad", label: "Kill", text: `Passed ${passed} of ${tests.length} of its pre-registered tests.` };
}

/** The path as an SVG polyline in a ``width`` x ``height`` box, with where zero and day 0
 * fall and a few round percentage ticks. Null with fewer than two known points. */
export function pathGeometry(
  path: EventStudy["path"],
  width: number,
  height: number,
): { points: string; zeroY: number; day0X: number; ticks: { y: number; label: string }[] } | null {
  const known = path.filter((p): p is { day: number; abnormal: number } => p.abnormal !== null);
  if (known.length < 2) return null;
  const days = known.map((p) => p.day);
  const values = known.map((p) => p.abnormal);
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const span = hi - lo || 1;
  const x0 = Math.min(...days);
  const xSpan = Math.max(...days) - x0 || 1;
  const x = (d: number) => ((d - x0) / xSpan) * width;
  const y = (v: number) => height - ((v - lo) / span) * height;
  const step = [0.05, 0.1, 0.2, 0.25, 0.5, 1].find((s) => span / s <= 5) ?? 1;
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) {
    ticks.push({ y: y(v), label: `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(Math.round(v * 100))}%` });
  }
  return {
    points: known.map((p) => `${x(p.day).toFixed(1)},${y(p.abnormal).toFixed(1)}`).join(" "),
    zeroY: y(0),
    day0X: x(0),
    ticks,
  };
}
