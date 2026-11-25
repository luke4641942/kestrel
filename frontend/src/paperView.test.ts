import { describe, expect, it } from "vitest";
import type { PaperDetail, Waiting } from "./api";
import {
  accountProfit,
  versusHolding,
  conditionText,
  decayView,
  holdingView,
  ruleText,
  waitingSummary,
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
    last_bar: "2026-11-14T00:00:00Z", equity: 331.5, cash: 222.4, position: 0.0016, positions: {}, halted: null, trade_count: 0, waiting: null,
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

  it("sets an account against holding its market over the same days", () => {
    const v = versusHolding(paperDetail({ equity: 329, initial_cash: 330, hold_return: -0.067 }));
    expect(v?.account).toBeCloseTo(-1 / 330);
    expect(v?.hold).toBe(-0.067);
    expect(v?.gap).toBeCloseTo(-1 / 330 + 0.067);
    expect(versusHolding(paperDetail({ hold_return: null }))).toBeNull();
    expect(versusHolding(paperDetail({ equity: null, hold_return: 0.1 }))).toBeNull();
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

describe("what an account is waiting for", () => {
  const test = (subject: string, met: boolean, move: number, label = "x") => ({
    label, subject, value: 100, level: 110, met, move,
  });
  const waiting = (over: Partial<Waiting>): Waiting => ({
    bar: "2026-11-21T00:00:00Z", rule: "all", decides: "every close", would_hold: [], conditions: [], ...over,
  });

  it("says what each test needs by the next close, or how far it can fall", () => {
    expect(conditionText(test("BTCUSDT", false, 0.069))).toBe("needs BTC +6.9% by the next close");
    expect(conditionText(test("ETHUSDT", true, -0.04))).toBe("holds unless ETH falls 4.0%");
  });

  it("names every missing move when all tests must hold", () => {
    const w = waiting({ conditions: [test("BTCUSDT", false, 0.28), test("ETHUSDT", false, 0.416)] });
    expect(waitingSummary(w)).toBe("Cash; needs BTC +28.0% and ETH +41.6% by the next close");
    expect(waitingSummary(waiting({ conditions: [test("BTCUSDT", true, -0.1)] }))).toBe("All tests hold: in");
    expect(waitingSummary(waiting({ decides: "Sunday closes", conditions: [test("BTCUSDT", true, -0.1)] }))).toBe(
      "Tests hold; decides on Sunday closes",
    );
  });

  it("names the nearest trend when any one buys a share", () => {
    const w = waiting({
      rule: "any",
      conditions: [test("BTCUSDT", false, 0.276), test("BTCUSDT", false, 0.402), test("BTCUSDT", false, 0.069)],
    });
    expect(waitingSummary(w)).toBe("Cash; nearest test: BTC +6.9% by the next close");
    const some = waiting({ rule: "any", conditions: [test("BTCUSDT", true, -0.05), test("BTCUSDT", false, 0.1)] });
    expect(waitingSummary(some)).toBe("1 of 2 hold; nearest test: BTC +10.0% by the next close");
    expect(waitingSummary(null)).toBe("n/a");
  });

  it("explains the rule and a rotation's picks", () => {
    const g1 = waiting({ decides: "Sunday closes", would_hold: ["BNBUSDT", "BTCUSDT"], conditions: [test("BTCUSDT", false, 0.3)] });
    expect(ruleText(g1)).toContain("Decides at Sunday closes only");
    expect(ruleText(g1)).toContain("If it bought now: BNB, BTC.");
    const tvt = waiting({ rule: "any", conditions: [test("BTCUSDT", false, 0.1), test("BTCUSDT", false, 0.1), test("BTCUSDT", false, 0.1)] });
    expect(ruleText(tvt)).toContain("each test that holds buys 1/3 of the position");
  });
});

describe("decayView", () => {
  const base = { days: 40, min_days: 90, percentile: 5, sharpe: null, floor: null, median: null, status: "too early" as const };
  it("says too early, and n/a without a check", () => {
    expect(decayView(base)).toEqual({ value: "Too early", tone: null, note: "40 of 90 days; then its Sharpe is checked against its backtest's" });
    expect(decayView(null).value).toBe("n/a");
    expect(decayView(undefined).value).toBe("n/a");
  });
  it("shows the Sharpe against the floor, and a flag as a prompt, never a verdict", () => {
    const ok = decayView({ ...base, days: 120, sharpe: 0.4, floor: -1.9, median: 1.1, status: "in line" });
    expect(ok).toEqual({ value: "In line", tone: "ok", note: "Sharpe 0.40 over 120 days; backtest 1-in-20 floor -1.90, median 1.10" });
    const bad = decayView({ ...base, days: 120, sharpe: -2.5, floor: -1.9, median: 1.1, status: "flagged" });
    expect(bad.tone).toBe("warn");
    expect(bad.note).toContain("nothing changes without Luke");
  });
});
