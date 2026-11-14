import { describe, expect, it } from "vitest";
import type { RegimeDetail } from "./api";
import { firstFitted, latestReading, probabilityLines, shadeAlpha, shading, stateColor, stateName } from "./regimes";

export function regimeDetail(over: Partial<RegimeDetail> = {}): RegimeDetail {
  return {
    symbol: "BTCUSDT",
    created_at: "2026-11-14T00:00:00Z",
    first: "2025-10-01T00:00:00Z",
    last: "2025-10-04T00:00:00Z",
    refits: 1,
    preset: { states: 3 },
    time: [100, 200, 300, 400],
    close: [10, 11, 12, 13],
    probabilities: [null, [0.7, 0.2, 0.1], [0.1, 0.3, 0.6], [0.2, 0.5, 0.3]],
    state: [null, 0, 2, 1],
    states: [0, 1, 2].map((state) => ({
      state,
      share: 1 / 3,
      mean_return: 0.001 * (1 - state),
      volatility: 0.4 + 0.2 * state,
      average_run: 10,
      expected_days: 20,
    })),
    transitions: [
      [0.97, 0.02, 0.01],
      [0.03, 0.94, 0.03],
      [0.0, 0.03, 0.97],
    ],
    oos_log_likelihood: 12.5,
    ...over,
  };
}

describe("regimes", () => {
  it("names states by their place in the volatility order", () => {
    expect([0, 1, 2].map((k) => stateName(k, 3))).toEqual(["Calm", "Normal", "Volatile"]);
    expect([0, 1].map((k) => stateName(k, 2))).toEqual(["Calm", "Volatile"]);
    expect(stateName(3, 5)).toBe("State 4");
    expect(stateColor(0)).toBe("--regime-1");
    expect(stateColor(7)).toBe("--regime-3");
  });

  it("shades only the days with a state", () => {
    expect(shading(regimeDetail())).toEqual([
      { time: 200, state: 0 },
      { time: 300, state: 2 },
      { time: 400, state: 1 },
    ]);
  });

  it("starts the states at the first fitted day, and tints calm days least", () => {
    expect(firstFitted(regimeDetail())).toBe(200);
    expect(firstFitted(regimeDetail({ state: [null, null, null, null] }))).toBeNull();
    expect([0, 1, 2, 5].map(shadeAlpha)).toEqual([0.06, 0.22, 0.42, 0.42]);
  });

  it("reads the last fitted day", () => {
    expect(latestReading(regimeDetail())).toEqual({ time: 400, state: 1, probability: 0.5 });
    expect(latestReading(regimeDetail({ state: [null, null, null, null], probabilities: [null, null, null, null] }))).toBeNull();
  });

  it("draws one probability line per state, without the unfitted days", () => {
    const lines = probabilityLines(regimeDetail());
    expect(lines).toHaveLength(3);
    expect(lines[2]).toEqual([
      { time: 200, value: 0.1 },
      { time: 300, value: 0.6 },
      { time: 400, value: 0.3 },
    ]);
  });
});
