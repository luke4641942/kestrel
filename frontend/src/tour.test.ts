import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { RunSummary } from "./api";
import { NAV } from "./App";
import { slug } from "./markdown";
import { pickRun, stepHash, TOUR, wantedTop } from "./tour";

const SRC = __dirname;
const sources = (dir: string): string[] =>
  readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) return sources(p);
    return /\.tsx$/.test(f) && !f.includes(".test.") ? [readFileSync(p, "utf8")] : [];
  });

describe("the tour's stops", () => {
  it("each has a chapter, and the chapters come in order", () => {
    const chapters = TOUR.map((s) => s.chapter);
    expect(chapters.every(Boolean)).toBe(true);
    const runs = chapters.filter((c, i) => c !== chapters[i - 1]);
    expect(new Set(runs).size).toBe(runs.length); // a chapter is never split
  });

  it("each opens a real page", () => {
    const routes = new Set(NAV.map((n) => n.route));
    for (const s of TOUR) {
      if (s.route !== "run") expect(routes.has(s.route.split("/")[0] as never)).toBe(true);
    }
  });

  it("each highlights something that exists, so a renamed section breaks a test, not the tour", () => {
    const code = sources(SRC).join("\n");
    const summary = readFileSync(join(SRC, "../../docs/research-summary.md"), "utf8");
    const headings = summary.split("\n").filter((l) => l.startsWith("#")).map((l) => slug(l.replace(/^#+\s*/, "")));
    for (const s of TOUR) {
      const named = new RegExp(`tour=(\\{[^}\\n]*)?"${s.target}"`); // tour="x", data-tour="x" or data-tour={... "x" ...}
      const inCode = named.test(code);
      expect(inCode || headings.includes(s.target), s.target).toBe(true);
    }
  });

  it("opens the strategies' frozen note that the tour names", () => {
    const note = TOUR.find((s) => s.route.startsWith("strategies/"))!;
    const slugDir = join(SRC, "../../research/strategies", note.route.split("/")[1]!);
    expect(statSync(slugDir).isDirectory()).toBe(true);
  });
});

describe("pickRun", () => {
  const run = (over: Partial<RunSummary>) =>
    ({
      id: "x", created_at: "2026-11-01T00:00:00Z", strategy: "trend-vol-target", symbol: "BTCUSDT", interval: "1d",
      start: "2020-01", end: "2025-10", research: null, ...over,
    }) as RunSummary;

  it("prefers the run under trend-vol-target's own note, else the newest on BTC", () => {
    const main = run({ id: "main", research: { slug: "trend-following-with-volatility-targeting-btc", version: 1 } });
    const newer = run({ id: "newer", created_at: "2026-11-15T00:00:00Z" });
    const eth = run({ id: "eth", symbol: "ETHUSDT", created_at: "2026-11-20T00:00:00Z" });
    expect(pickRun([newer, main, eth])).toBe("main");
    expect(pickRun([newer, eth])).toBe("newer");
    expect(pickRun([eth])).toBeNull();
  });

  it("turns the run stop into a link, or the Backtests page without a run", () => {
    const stop = TOUR.find((s) => s.route === "run")!;
    expect(stepHash(stop, "abc")).toBe("/backtests/abc");
    expect(stepHash(stop, null)).toBe("/backtests");
    expect(stepHash(TOUR[0]!, null)).toBe("/thesis");
  });
});

describe("wantedTop", () => {
  // A 900px screen, a 60px top bar and a 240px card leave 900 - 60 - 240 - 32 = 568px.
  it("centres an element in the space between the top bar and the card", () => {
    expect(wantedTop(168, 900, 60, 240)).toBe(60 + 16 + 200);
  });

  it("reads a tall element from its top", () => {
    expect(wantedTop(700, 900, 60, 240)).toBe(76);
    expect(wantedTop(520, 900, 60, 240)).toBe(76); // over 90% of the space
  });
});
