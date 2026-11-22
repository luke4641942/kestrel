import { describe, expect, it } from "vitest";
import type { SignalStudy } from "./api";
import { sideText, signalVerdict, weekPct } from "./signalStudies";

const side = (spread: number, p: number) => ({ n_on: 100, n_off: 110, mean_on: spread, mean_off: 0, spread, p_positive: p });

export function signalStudy(over: Partial<SignalStudy> = {}): SignalStudy {
  return {
    signal: "variance-premium",
    title: "A high variance premium",
    note: "does-a-high-variance-premium-lead-prices",
    measured_at: "2026-11-22T10:00:00Z",
    commit: "abc123",
    verdict: "KILL",
    markets: ["BTCUSDT", "ETHUSDT"],
    hurdle: 0.0017,
    weeks: side(0.0073, 0.81),
    past_week_rose: { BTCUSDT: { on: 0.53, off: 0.48 } },
    after_rise: side(-0.002, 0.4),
    after_fall: side(0.0145, 0.9),
    control_holds: false,
    beside: null,
    report: "Signal study ... Verdict: KILL",
    ...over,
  };
}

describe("signalVerdict", () => {
  it("names every reason a study was killed", () => {
    const v = signalVerdict(signalStudy());
    expect(v.label).toBe("Kill");
    expect(v.text).toContain("P 81% is below the 90% needed");
    expect(v.text).toContain("momentum or dip-buying in disguise");
  });
  it("reads a pass as a study, not a strategy", () => {
    const v = signalVerdict(signalStudy({ verdict: "PROCEED", weeks: side(0.013, 0.96), control_holds: true }));
    expect(v.tone).toBe("ok");
    expect(v.text).toContain("after rising and falling weeks alike");
    expect(v.text).toContain("not a strategy");
  });
  it("shows log returns as simple percentages", () => {
    expect(weekPct(Math.log(1.01))).toBe("+1.00%");
    expect(weekPct(-0.002)).toBe("−0.20%");
    expect(weekPct(null)).toBe("n/a");
    expect(sideText(side(Math.log(1.0145), 0.9))).toBe("+1.45% (100 on, 110 off)");
  });
});
