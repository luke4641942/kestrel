import { describe, expect, it } from "vitest";
import type { PaperDetail } from "./api";
import {
  accountProfit,
  holdingView,
  isBasket,
  targetsText,
  totalView,
  tradeReturn,
  tradesSummary,
  trackingVerdict,
  venueLabel,
} from "./paperView";

export function paperDetail(over: Partial<PaperDetail> = {}, tracking: Partial<PaperDetail["tracking"]> = {}): PaperDetail {
  return {
    name: "btc", strategy: "trend-vol-target", symbol: "BTCUSDT", interval: "1d", venue: "testnet",
    research: "holdout-trend-vol-target-btc", initial_cash: 330, trade_from: "2026-11-13T00:00:00Z",
    last_bar: "2026-11-14T00:00:00Z", equity: 331.5, cash: 222.4, position: 0.0016, positions: {}, halted: null, trade_count: 0,
    equity_series: { time: [1, 2], equity: [330, 331.5], drawdown: [0, 0] },
    expected: { time: [1, 2], equity: [330, 332], drawdown: [0, 0] },
    fills: [], trades: [], decisions: [], events: [],
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

describe("baskets", () => {
  it("shows a single coin's holding, a basket's coins, or cash", () => {
    expect(holdingView(paperDetail())).toEqual({ value: "0.0016 BTC", note: "Cash 222.40 USDT" });
    const basket = paperDetail({ symbol: "UNIVERSE", positions: { SOLUSDT: 0.5, DOGEUSDT: 300 } });
    expect(isBasket(basket)).toBe(true);
    expect(holdingView(basket)).toEqual({ value: "2 coins", note: "300 DOGE, 0.5 SOL; cash 222.40 usdt" });
    expect(holdingView(paperDetail({ symbol: "UNIVERSE" })).value).toBe("Cash only");
  });

  it("puts a basket decision in a line, largest first", () => {
    expect(targetsText({ SOLUSDT: 0.15, DOGEUSDT: 0.17, BTCUSDT: null })).toBe("DOGE 17%, SOL 15%, BTC kept");
    expect(targetsText({})).toBe("Cash");
  });
});

describe("totalView", () => {
  const total = { accounts: 2, initial_cash: 660, equity: 693, expected_equity: 700, profit: 33,
    equity_series: { time: [], equity: [], drawdown: [] }, expected: { time: [], equity: [], drawdown: [] } };
  it("gives net profit, its share of the starting cash, and the gap to the model", () => {
    const v = totalView(total)!;
    expect(v.profit).toBe(33);
    expect(v.share).toBeCloseTo(0.05);
    expect(v.model).toBeCloseTo(-0.01);
  });
  it("is empty before any mark", () => {
    expect(totalView({ ...total, equity: null, profit: null })).toBeNull();
  });
});

describe("profit and trades", () => {
  const trade = (profit: number | null, closed: string | null = "2026-11-18T00:00:00Z") => ({
    symbol: "BTCUSDT", opened: "2026-11-15T00:00:00Z", closed, bought: 100, sold: 100 + (profit ?? 0),
    value: closed ? null : 101, profit, fees: 0, fills: 2,
  });
  it("gives profit since the account opened", () => {
    expect(accountProfit(paperDetail({ equity: 333, initial_cash: 330 }))).toEqual({ profit: 3, share: 3 / 330 });
    expect(accountProfit(paperDetail({ equity: null }))).toBeNull();
  });
  it("counts trades, wins and losses, and what the closed ones made", () => {
    const s = tradesSummary([trade(5), trade(-2), trade(1, null)]);
    expect(s.text).toBe("2 closed (1 won, 1 lost), 1 open");
    expect(s.realised).toBe(3);
    expect(tradesSummary([]).text).toBe("No trades yet");
    expect(tradeReturn(trade(5))).toBeCloseTo(0.05);
    expect(tradeReturn(trade(null, null))).toBeNull();
  });
});
