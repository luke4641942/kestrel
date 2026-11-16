import { describe, expect, it } from "vitest";
import type { ShortStudy } from "./api";
import { shortVerdict, studyName } from "./shortStudies";

export function shortStudy(over: Partial<ShortStudy> = {}): ShortStudy {
  return {
    name: "cash-and-carry",
    note: "cash-and-carry-on-btc-eth-and-sol-perps",
    measured_at: "2026-11-16T09:00:00Z",
    commit: "abc123",
    verdict: "NEEDS-SHORTING",
    tests: { "beats a 4% stablecoin yield": true, "at least 30 trades": true },
    report: "Cash-and-carry ... Verdict: NEEDS-SHORTING",
    curves: [
      { label: "Cash-and-carry", series: { time: [1, 2], equity: [1, 1.07], drawdown: [0, 0] } },
      { label: "A 4% stablecoin yield", series: { time: [1, 2], equity: [1, 1.04], drawdown: [0, 0] } },
    ],
    ...over,
  };
}

describe("shortVerdict", () => {
  it("says a pass can't be traded", () => {
    const v = shortVerdict(shortStudy());
    expect(v.tone).toBe("ok");
    expect(v.label).toBe("Needs shorting");
    expect(v.text).toMatch(/^Passed all 2 .* it isn't traded\.$/);
  });

  it("counts a failure's tests", () => {
    const v = shortVerdict(shortStudy({ verdict: "DROP", tests: { a: true, b: false, c: false } }));
    expect(v).toEqual({ tone: "bad", label: "Failed", text: "Passed 1 of 3 of its pre-registered tests." });
  });

  it("names each study", () => {
    expect(studyName({ name: "cash-and-carry" })).toBe("Cash-and-carry on BTC, ETH and SOL perps");
    expect(studyName({ name: "new-one" })).toBe("new-one");
  });
});
