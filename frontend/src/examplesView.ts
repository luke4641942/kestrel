// Worked examples of a strategy's rule (Strategies page): a few random entries and exits,
// each with the weight chosen and the tests behind it, to check against the note's wording
// before its first run. Kept out of the component so it can be tested directly.
import type { Example } from "./api";

const num = (x: number) => x.toLocaleString("en-AU", { maximumSignificantDigits: 6 });

export function exampleTitle(e: Example): string {
  const day = e.open_time.slice(0, 16).replace("T", " ");
  return `${e.kind === "entry" ? "Entry" : "Exit"} at the close of ${day} UTC`;
}

export function weightLine(e: Example): string {
  return `Weight ${e.before.toFixed(2)} → ${e.after.toFixed(2)}, close ${num(e.close)}; trades at the next open`;
}

export function testLines(e: Example): { text: string; met: boolean }[] {
  return e.conditions.map((c) => ({
    text: `${c.label}: ${c.subject} ${num(c.value)} against ${num(c.level)}`,
    met: c.met,
  }));
}

export function ruleLine(e: Example): string | null {
  if (!e.rule) return null;
  return e.rule === "all" ? "In the market when every test holds" : "Sized by how many tests hold";
}

export function agreement(e: Example): { text: string; tone: "ok" | "bad" | "neutral" } {
  if (e.agrees === true) return { text: "Tests and weight agree", tone: "ok" };
  if (e.agrees === false) return { text: "Tests and weight disagree: a bug, report it", tone: "bad" };
  return { text: "This strategy doesn't describe its tests; check the weight against the note", tone: "neutral" };
}
