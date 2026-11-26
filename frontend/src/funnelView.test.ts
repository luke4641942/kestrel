import { describe, expect, it } from "vitest";
import type { Funnel, PoolFunnel } from "./api";
import { keptLine, poolsFor, stages } from "./funnelView";

const main: PoolFunnel = {
  pool: "main", trials: 880, positive: 778, beat_holding: 318, no_holding: 9, above_bar: 0,
  luck_bar: 2.2796, cleared_luck: 0, unscored: 0, passed_own_test: null,
};
const confirmation: PoolFunnel = {
  pool: "confirmation", trials: 2, positive: 2, beat_holding: 2, no_holding: 0, above_bar: 2,
  luck_bar: 0.05, cleared_luck: 0, unscored: 2, passed_own_test: 1,
};
const forex: PoolFunnel = { ...main, pool: "forex", trials: 2, positive: 0, beat_holding: 0, no_holding: 2 };
const funnel: Funnel = {
  pools: [main, confirmation, forex],
  kept: { decisions: { pursue: 6, watch: 2, "needs-shorting": 1, drop: 48 }, holdouts: 10, holdouts_passed: 5, paper_accounts: 8 },
  all_trials: 884,
};

describe("the funnel", () => {
  it("lays every stage out as a share of the pool's trials", () => {
    const s = stages(main);
    expect(s.map((x) => x.count)).toEqual([880, 778, 318, 0, 0]);
    expect(s[0]?.share).toBe(1);
    expect(s[2]?.share).toBeCloseTo(318 / 880);
    expect(s[3]?.label).toBe("Above the luck bar (Sharpe 2.28)");
    expect(s[2]?.note).toContain("9 with no holding comparison stored");
  });

  it("never counts an unscored trial as a pass, and shows a confirmation run's own test", () => {
    const s = stages(confirmation);
    expect(s[4]?.count).toBe(0);
    expect(s[4]?.note).toContain("2 above the bar can't be deflated");
    expect(s[5]).toMatchObject({ label: "Passed its frozen note's own test", count: 1 });
    expect(stages(main)).toHaveLength(5);
  });

  it("shows each market its own pools", () => {
    expect(poolsFor(funnel, "all").map((p) => p.pool)).toEqual(["main", "confirmation", "forex"]);
    expect(poolsFor(funnel, "crypto").map((p) => p.pool)).toEqual(["main", "confirmation"]);
    expect(poolsFor(funnel, "forex").map((p) => p.pool)).toEqual(["forex"]);
    expect(poolsFor(funnel, "futures")).toEqual([]);
  });

  it("says plainly that pursued is not passed", () => {
    const line = keptLine(funnel);
    expect(line).toContain("6 pursue, 2 watch, 1 needs-shorting, 48 drop");
    expect(line).toContain("5 of 10 holdout tests passed; 8 paper accounts");
    expect(line).toContain("no trial has cleared the luck test");
    expect(line).toContain("884 trials across all pools");
    const passed = { ...funnel, pools: [{ ...main, cleared_luck: 1 }] };
    expect(keptLine(passed)).toContain("1 trial cleared the luck test");
  });
});
