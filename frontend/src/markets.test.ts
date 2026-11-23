import { describe, expect, it } from "vitest";
import type { Decision, FxRun, ResearchSummary, Validation } from "./api";
import { cellText, classOf, fxRunRow, inMarket, marketGrid, marketNotice, poolBar, poolLine } from "./markets";

const decision = (strategy: string, d: Decision["decision"], asset_class?: string): Decision => ({
  strategy,
  name: strategy,
  market: "X 1d",
  decision: d,
  decided: "2026-11-23",
  reason: "r",
  evidence: [],
  asset_class,
});

const note = (implementation: string | null, status: ResearchSummary["status"], asset_class?: string): ResearchSummary => ({
  slug: `${implementation}-${status}`,
  name: "n",
  status,
  created_at: "2026-11-23T00:00:00Z",
  version: 1,
  frozen: true,
  implementation,
  runs: 0,
  trials: 0,
  asset_class,
});

const V: Validation = {
  trials: 872,
  sharpe_std: 0.7,
  expected_max_sharpe: 2.27,
  studies: [],
  pools: { main: 872, confirmation: 2, forex: 1 },
  all_trials: 875,
  pool_bars: { main: 2.27, confirmation: 0.3, forex: 0 },
};

describe("markets", () => {
  it("reads an item's market, crypto when it doesn't say", () => {
    expect(classOf({ asset_class: "forex" })).toBe("forex");
    expect(classOf({})).toBe("crypto");
    expect(inMarket({ asset_class: "forex" }, "crypto")).toBe(false);
    expect(inMarket({ asset_class: "forex" }, "all")).toBe(true);
  });

  it("says when a page is crypto-only, and that futures is parked", () => {
    expect(marketNotice("trading", "all")).toBeUndefined();
    expect(marketNotice("research", "forex")).toBeUndefined();
    expect(marketNotice("trading", "forex")).toEqual({ text: expect.stringContaining("crypto accounts only"), showingCrypto: true });
    expect(marketNotice("research", "futures")?.showingCrypto).toBe(false);
    expect(marketNotice("charts", "futures")?.text).toMatch(/^Futures is parked.*Charts read the crypto store/);
  });

  it("gives each pool its own bar, with every pool's total beside it", () => {
    expect(poolLine(V, "crypto")).toBe("luck bar 2.27 from 872 trials · 875 across all pools");
    expect(poolLine(V, "forex")).toBe("1 trial, no luck bar yet · 875 across all pools");
    expect(poolBar(V, "crypto")).toBe("2.27");
    expect(poolBar(V, "forex")).toBe("none yet");
    expect(poolBar(undefined, "forex")).toBe("–");
  });

  it("lays families against markets: best decision, open notes, or not tried", () => {
    const grid = marketGrid(
      [decision("trend-vol-target", "pursue"), decision("trend-trio", "drop"), decision("rsi-dip", "drop")],
      [note("fx:trend", "specified", "forex"), note("rsi-dip", "rejected"), note("fx:trend", "rejected", "forex")],
    );
    const trend = grid.find((r) => r.family.key === "trend")!;
    expect(cellText(trend.cells.crypto)).toBe("pursue · 2 decisions");
    expect(cellText(trend.cells.forex)).toBe("1 note open");
    expect(cellText(trend.cells.futures)).toBe("not tried");
    const reversal = grid.find((r) => r.family.key === "reversal")!;
    expect(cellText(reversal.cells.crypto)).toBe("drop · 1 decision");
    expect(grid.some((r) => r.family.key === "breakout")).toBe(false); // tried nowhere: left out
  });

  it("formats a forex run's row", () => {
    const run: FxRun = {
      id: "r",
      created_at: "2026-11-23T00:00:00Z",
      strategy: "forex trend",
      symbol: "EURUSD,GBPUSD",
      start: "2006-02",
      end: "2022-12",
      initial_cash: 500,
      account_currency: "AUD",
      research: { slug: "n", version: 1 },
      params: {},
      metrics: {
        sharpe: 0.31,
        total_return: 0.42,
        max_drawdown: -0.183,
        average_gross: 2.4,
        financing: -37.6,
        spread_costs: 12,
        trades: 900,
        windows: 8,
        windows_positive: 5,
        wiped_out: false,
      },
      deflated_sharpe: 0.81,
      deflated_trials: 1,
      asset_class: "forex",
    };
    expect(fxRunRow(run)).toEqual({
      period: "2006-02 to 2022-12",
      pairs: "2 pairs",
      ret: "+42.0%",
      sharpe: "0.31",
      deflated: "81.0%",
      drawdown: "-18.3%",
      gross: "2.40x",
      financing: "-38 AUD",
      windows: "5 of 8",
    });
    expect(fxRunRow({ ...run, metrics: { ...run.metrics, wiped_out: true } }).ret).toBe("wiped out");
  });
});
