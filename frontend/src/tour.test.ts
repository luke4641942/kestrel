import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { RunSummary } from "./api";
import { NAV } from "./App";
import { slug } from "./markdown";
import { pickRun, placeCard, stepHash, TOUR } from "./tour";

const SRC = __dirname;
const sources = (dir: string): string[] =>
  readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) return sources(p);
    return /\.tsx$/.test(f) && !f.includes(".test.") ? [readFileSync(p, "utf8")] : [];
  });

describe("the tour's stops", () => {
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
      const inCode = code.includes(`tour="${s.target}"`) || code.includes(`data-tour="${s.target}"`);
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

describe("placeCard", () => {
  const card = { width: 360, height: 200 };
  const screen = { width: 1300, height: 900 };

  it("sits beside a narrow element, covering nothing", () => {
    expect(placeCard({ top: 400, left: 60, width: 700, height: 36 }, card, screen)).toEqual({ top: 400, left: 772, sheet: false });
    expect(placeCard({ top: 800, left: 60, width: 700, height: 36 }, card, screen).top).toBe(900 - 200 - 12); // kept on screen
  });

  it("goes below a wide element, else above, inside the screen", () => {
    expect(placeCard({ top: 100, left: 40, width: 1200, height: 200 }, card, screen)).toEqual({ top: 312, left: 40, sheet: false });
    expect(placeCard({ top: 600, left: 40, width: 1200, height: 250 }, card, screen)).toEqual({ top: 388, left: 40, sheet: false });
    expect(placeCard({ top: 100, left: 1000, width: 280, height: 40 }, card, screen).left).toBe(1300 - 360 - 12);
  });

  it("becomes a bottom sheet on a phone, or when there's nothing to point at", () => {
    expect(placeCard({ top: 100, left: 0, width: 300, height: 50 }, card, { width: 390, height: 800 }).sheet).toBe(true);
    expect(placeCard(null, card, screen).sheet).toBe(true);
  });
});
