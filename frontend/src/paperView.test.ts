import { describe, expect, it } from "vitest";
import type { PaperDetail } from "./api";
import { trackingVerdict, venueLabel } from "./paperView";

export function paperDetail(over: Partial<PaperDetail> = {}, tracking: Partial<PaperDetail["tracking"]> = {}): PaperDetail {
  return {
    name: "btc", strategy: "trend-vol-target", symbol: "BTCUSDT", interval: "1d", venue: "testnet",
    research: "holdout-trend-vol-target-btc", initial_cash: 330, trade_from: "2026-11-13T00:00:00Z",
    last_bar: "2026-11-14T00:00:00Z", equity: 331.5, cash: 222.4, position: 0.0016, halted: null,
    equity_series: { time: [1, 2], equity: [330, 331.5], drawdown: [0, 0] },
    expected: { time: [1, 2], equity: [330, 332], drawdown: [0, 0] },
    fills: [], decisions: [], events: [],
    tracking: { days: 2, decision_mismatches: 0, missed: 0, difference: -0.0015, cost_bps: 12.4, expected_bps: 11.3, ...tracking },
    ...over,
  };
}

describe("trackingVerdict", () => {
  it("leads with the decisions, then equity and costs against the model", () => {
    expect(trackingVerdict(paperDetail())).toEqual({
      tone: "ok",
      text: "Decisions match the model on all 2 days. Equity is -0.15% against the model. Fills cost 12.4 bps each against 11.3 modelled.",
    });
  });

  it("calls a decision mismatch a bug", () => {
    const v = trackingVerdict(paperDetail({}, { decision_mismatches: 1 }));
    expect(v.tone).toBe("bad");
    expect(v.text).toMatch(/^1 of 2 decisions differ from the model/);
  });

  it("warns on missed days or drift, and waits when there is nothing yet", () => {
    expect(trackingVerdict(paperDetail({}, { missed: 1 })).text).toMatch(/1 day was missed/);
    expect(trackingVerdict(paperDetail({}, { missed: 3 })).tone).toBe("warn");
    expect(trackingVerdict(paperDetail({}, { difference: 0.05 })).tone).toBe("warn");
    expect(trackingVerdict(paperDetail({}, { days: 0 })).tone).toBe("neutral");
    expect(trackingVerdict(paperDetail({}, { difference: null, cost_bps: null })).text).toBe("Decisions match the model on all 2 days.");
  });

  it("names the venue", () => {
    expect([venueLabel("testnet"), venueLabel("sim"), venueLabel("live")]).toEqual(["Binance testnet", "Simulated", "live"]);
  });
});
