import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { Decision } from "./api";
import { FAMILIES, byFamily, familyOf, luckReadout, luckSeries, tally } from "./researchView";

const decision = (strategy: string, verdict: Decision["decision"], market = "BTCUSDT 1d"): Decision => ({
  strategy,
  name: strategy,
  market,
  decision: verdict,
  decided: "2026-11-16",
  reason: "because",
  evidence: [],
});

describe("research families", () => {
  it("puts every strategy in research/decisions.toml in a family", () => {
    const toml = readFileSync(resolve(__dirname, "../../research/decisions.toml"), "utf8");
    const keys = [...toml.matchAll(/^strategy = "([^"]+)"/gm)].map((m) => m[1]!);
    expect(keys.length).toBeGreaterThan(0);
    expect(keys.filter((k) => familyOf(k).key === "other")).toEqual([]);
  });

  it("gives each strategy key one family at most", () => {
    const all = FAMILIES.flatMap((f) => f.strategies);
    expect(new Set(all).size).toBe(all.length);
  });

  it("groups decisions in family order, leaves out empty families and puts unknowns last", () => {
    const rows = byFamily([
      decision("rsi-dip", "drop"),
      decision("trend-vol-target", "pursue"),
      decision("trend-vol-target", "drop", "EURUSDT 1d"),
      decision("something-new", "watch"),
    ]);
    expect(rows.map((r) => r.family.key)).toEqual(["trend", "reversal", "other"]);
    expect(rows[0]).toMatchObject({ pursue: 1, watch: 0, drop: 1 });
    expect(rows[0]!.decisions).toHaveLength(2);
  });

  it("tallies outcomes without zeros", () => {
    expect(tally({ pursue: 1, watch: 0, drop: 5 })).toBe("1 pursue · 5 drop");
    expect(tally({ pursue: 0, watch: 2, drop: 0 })).toBe("2 watch");
  });
});

describe("the luck bar series", () => {
  it("converts to seconds and keeps the last point within a second", () => {
    const series = luckSeries([
      { at: "2026-11-12T01:00:00.100Z", trials: 1, luck_bar: 0 },
      { at: "2026-11-12T01:00:00.900Z", trials: 2, luck_bar: 0.5 },
      { at: "2026-11-15T15:46:57Z", trials: 751, luck_bar: 2.23 },
    ]);
    expect(series).toHaveLength(2);
    expect(series[0]).toEqual({ time: Date.parse("2026-11-12T01:00:00Z") / 1000, value: 0.5, trials: 2 });
    expect(luckReadout(series[1]!)).toBe("2026-11-15: 751 trials, luck bar 2.23");
  });
});

describe("confirmation runs", () => {
  it("reads each run's summary and sorts its coins by how far ahead of holding", async () => {
    const { confirmationRuns } = await import("./researchView");
    const study = {
      id: "20261115-231551-21a19d",
      created_at: "2026-11-15T23:15:51Z",
      kind: "confirmation" as const,
      strategy: "btc-regime-trend",
      symbol: "CONFIRMATION",
      interval: "1d" as const,
      start: "first bar",
      end: "2025-11 (exclusive)",
      looks: [],
      verdict: "PASS",
      confirmation: true,
      detail: {
        note: "confirmation-btc-regime-and-coin-trend",
        coins: 3,
        beats_share: 2 / 3,
        windows: 10,
        windows_share: 0.6,
        median_sharpe: 0.57,
        per_coin: [
          { symbol: "AAA", sharpe: 0.1, held_sharpe: 0.5, windows: 4, windows_beating: 1 },
          { symbol: "BBB", sharpe: 0.9, held_sharpe: -0.4, windows: 6, windows_beating: 5 },
          { symbol: "CCC", sharpe: null, held_sharpe: 0.2, windows: 0, windows_beating: 0 },
        ],
      },
    };
    const robustness = { ...study, id: "x", kind: "robustness" as const, detail: {} };
    const [run, ...rest] = confirmationRuns([robustness, study]);
    expect(rest).toEqual([]);
    expect(run).toMatchObject({ note: "confirmation-btc-regime-and-coin-trend", verdict: "PASS", coins: 3, windows: 10 });
    expect(run!.perCoin.map((c) => c.symbol)).toEqual(["BBB", "AAA", "CCC"]);
    expect(run!.perCoin[0]!.gap).toBeCloseTo(1.3);
    expect(run!.perCoin[2]!.gap).toBeNull();
  });
});
