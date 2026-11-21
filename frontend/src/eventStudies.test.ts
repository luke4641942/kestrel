import { describe, expect, it } from "vitest";
import type { EventStudy } from "./api";
import { eventName, eventVerdict, pathGeometry } from "./eventStudies";

export function eventStudy(over: Partial<EventStudy> = {}): EventStudy {
  return {
    name: "perp-listing",
    note: "does-a-perp-listing-mark-a-top",
    measured_at: "2026-11-18T09:00:00Z",
    commit: "abc123",
    verdict: "EXIT RULE",
    events: 102,
    event_days: 97,
    mean: -0.1515,
    p_below: 1,
    share_negative: 0.75,
    placebo_median: -0.0125,
    placebo_share_at_or_below: 0,
    tests: { "at least 50 event days": true, "mean at most -3%": true },
    path: [
      { day: -30, abnormal: 0 },
      { day: 0, abnormal: 0.9 },
      { day: 30, abnormal: 0.6 },
    ],
    report: "Perp listings ... Verdict: EXIT RULE",
    ...over,
  };
}

describe("eventVerdict", () => {
  it("calls a pass an exit rule, not a trade", () => {
    const v = eventVerdict(eventStudy());
    expect(v.label).toBe("Exit rule");
    expect(v.text).toMatch(/not a trade by itself/);
  });
  it("counts a kill's tests and flags too few events", () => {
    expect(eventVerdict(eventStudy({ verdict: "KILL", tests: { a: true, b: false } })).text).toBe(
      "Passed 1 of 2 of its pre-registered tests.",
    );
    expect(eventVerdict(eventStudy({ verdict: "INSUFFICIENT" })).tone).toBe("warn");
  });
  it("names each study", () => {
    expect(eventName({ name: "token-unlocks" })).toBe("Large token unlocks");
  });
});

describe("pathGeometry", () => {
  it("places the line, zero and day 0 in the box", () => {
    const g = pathGeometry(eventStudy().path, 600, 200)!;
    expect(g.points).toBe("0.0,200.0 300.0,0.0 600.0,66.7"); // 0 at the bottom, +90% at the top
    expect(g.zeroY).toBe(200);
    expect(g.day0X).toBe(300);
    expect(g.ticks.map((t) => t.label)).toEqual(["0%", "+20%", "+40%", "+60%", "+80%"]);
  });
  it("needs two known points", () => {
    expect(pathGeometry([{ day: 0, abnormal: null }, { day: 1, abnormal: 0.1 }], 600, 200)).toBeNull();
  });
});
