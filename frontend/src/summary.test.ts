import { describe, expect, it } from "vitest";
import { KIND_LABEL, MAX_LABELLED_TRADES, costHeadroom, heldAsset, money, span, trackRecord, tradeMarkers, verdict } from "./summary";
import { run, trade } from "./test/fixtures";

describe("verdict", () => {
  it("says the benchmark is the benchmark", () => {
    const v = verdict(run({ strategy: "buy-and-hold" }, { final_equity: 771.71 }));
    expect(v.tone).toBe("neutral");
    expect(v.headline).toBe("The benchmark: simply holding BTC made 441.71 USDT.");
  });

  it("says when a strategy lost money", () => {
    const v = verdict(run({}, { final_equity: 300 }));
    expect(v.tone).toBe("bad");
    expect(v.headline).toBe("Lost money: 30.00 USDT down after costs.");
    expect(v.profit).toBe(-30);
  });

  it("treats exactly break-even as not having made money", () => {
    expect(verdict(run({}, { final_equity: 330 })).tone).toBe("bad");
  });

  it("says when it made money but less than holding BTC", () => {
    const v = verdict(run({}, { final_equity: 331.63 }, 771.71));
    expect(v.tone).toBe("warn");
    expect(v.headline).toBe("Made money, but less than just holding BTC (441.71 USDT).");
    expect(v.vsBtc).toBeCloseTo(-440.08, 2);
  });

  it("says when it beat holding BTC", () => {
    const v = verdict(run({}, { final_equity: 800 }, 700));
    expect(v.tone).toBe("ok");
    expect(v.headline).toBe("Beat holding BTC by 100.00 USDT after costs.");
  });

  it.each([
    [{ few_trades: true, trades: 1 }, "Only 1 trade: too few"],
    [{ few_trades: true, trades: 12 }, "Only 12 trades: too few"],
    [{ psr: null }, "nothing to measure"],
    [{ psr: 0.97 }, "Statistically convincing: 97%"],
    [{ psr: 0.95 }, "Statistically convincing: 95%"],
    [{ psr: 0.94 }, "Promising but not proven: 94%"],
    [{ psr: 0.8 }, "Promising but not proven: 80%"],
    [{ psr: 0.54 }, "Not convincing: only a 54%"],
  ])("trust line for %o", (m, text) => {
    expect(verdict(run({}, m)).trust).toContain(text);
  });

  it("counts round trips and wins for bracket runs", () => {
    const r = run({ style: "brackets", trades: [trade(3), trade(-1), trade(-1)] }, { trades: 6, round_trips: 3 });
    const v = verdict(r);
    expect(v.trades).toBe(3); // not the 6 fills
    expect(v.wins).toBe(1);
  });
});

describe("span", () => {
  it.each([
    ["2024-01-01", "2024-01-13", "12 days"],
    ["2024-01-01", "2024-08-01", "7 months"],
    ["2024-01-01", "2025-01-01", "1 year"],
    ["2022-01-01", "2025-11-01", "3 years 10 months"],
    ["2022-01-01", "2023-12-30", "2 years"], // 11.97 months rounds up into a whole year
  ])("%s to %s is %s", (start, end, text) => {
    expect(span(start, end)).toBe(text);
  });
});

describe("money", () => {
  it("signs with a real minus sign", () => {
    expect(money(1.634)).toBe("+1.63");
    expect(money(-440.08)).toBe("−440.08");
    expect(money(1234.5)).toBe("+1,234.50");
  });
});

describe("tradeMarkers", () => {
  it("marks entries and labels exits by result for small bracket runs", () => {
    const markers = tradeMarkers(run({ style: "brackets", trades: [trade(2.96), trade(-1.03)] }));
    expect(markers).toHaveLength(4);
    expect(markers[0]).toMatchObject({ side: "buy" });
    expect(markers[1]).toMatchObject({ side: "sell", good: true, label: "+3.0R" });
    expect(markers[3]).toMatchObject({ side: "sell", good: false, label: "-1.0R" });
  });

  it("drops labels when there are too many trades to read", () => {
    const trades = Array.from({ length: MAX_LABELLED_TRADES + 1 }, () => trade(1));
    const exits = tradeMarkers(run({ style: "brackets", trades })).filter((m) => m.side === "sell");
    expect(exits.every((m) => m.label === undefined && m.good === true)).toBe(true);
  });

  it("marks buys and sells for weight runs", () => {
    const fill = { quantity: 1, reference_price: 1, price: 1, fee: 0, spread_cost: 0, impact_cost: 0, cash_flow: 0 };
    const markers = tradeMarkers(
      run({
        fills: [
          { ...fill, time: "2023-01-01T00:00:00Z", side: "buy" },
          { ...fill, time: "2023-02-01T00:00:00Z", side: "sell" },
        ],
      }),
    );
    expect(markers.map((m) => m.side)).toEqual(["buy", "sell"]);
    expect(markers[0]!.time).toBe(Date.UTC(2023, 0, 1) / 1000);
  });
});

describe("trackRecord", () => {
  it.each([
    [null, "Never, at this Sharpe"],
    [100, "About 3 months"],
    [5, "About 1 month"],
    [2095, "About 5.7 years"],
    [367_839, "More than 50 years"],
  ])("%s days -> %s", (days, text) => {
    expect(trackRecord(days)).toBe(text);
  });
});

describe("costHeadroom", () => {
  it.each([
    [null, 22, "No trades"],
    [20, 22, "No room"],
    [29, 22, "Fragile: costs could only rise 1.3x"],
    [184, 22, "Robust: costs could rise 8x"],
  ])("break-even %s vs paying %s", (be, paying, text) => {
    expect(costHeadroom(be, paying)).toContain(text);
  });
});

describe("the benchmark is the run's own market", () => {
  // Regression: an ETHUSDT run said "holding BTC" while comparing with holding ETH.
  it("names the asset from the symbol", () => {
    expect(heldAsset({ symbol: "ETHUSDT", account_currency: "USDT" })).toBe("ETH");
    expect(heldAsset({ symbol: "BTCUSDT", account_currency: "USDT" })).toBe("BTC");
    expect(heldAsset({ symbol: "ODDPAIR", account_currency: "USDT" })).toBe("ODDPAIR");
  });

  it("says ETH in an ETH run's verdict", () => {
    const eth = run({ symbol: "ETHUSDT" }, { final_equity: 295.22 }, 9740.42);
    expect(verdict(eth).headline).toBe("Lost money: 34.78 USDT down after costs.");
    const better = run({ symbol: "ETHUSDT" }, { final_equity: 400 }, 9740.42);
    expect(verdict(better).headline).toBe("Made money, but less than just holding ETH (9,410.42 USDT).");
    expect(verdict(run({ symbol: "ETHUSDT" }, { final_equity: 9800 }, 9740.42)).headline).toContain("Beat holding ETH");
    expect(verdict(run({ symbol: "ETHUSDT", strategy: "buy-and-hold" }, { final_equity: 9740.42 })).headline).toContain(
      "simply holding ETH",
    );
  });
});

describe("KIND_LABEL", () => {
  it("calls a control strategy a comparison", () => {
    expect(KIND_LABEL.control).toBe("comparison");
    expect(KIND_LABEL["engine-test"]).toBe("engine test");
  });
});
