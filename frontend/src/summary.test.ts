import { describe, expect, it } from "vitest";
import { breakEvenLabel, drawdownPaths, mostPromising, newestFirst, gateTone, holdoutEdge, datasetsLine, scorecardHeadline, luckBar, splitClosed, startDateVerdict, windowLabel, DECISION, studyTitle, studyTone, deflatedNote, spreadBands, DEFAULT_RUN_SORT, KIND_LABEL, UNIVERSE, marketLabel, marketsFor, defaultDataset, nextSort, sortRuns, fewTrades, signalsCaption, MAX_LABELLED_TRADES, MAX_MARKED_TRADES, costHeadroom, paramsText, runRole, tradeCount, heldAsset, money, span, trackRecord, tradeMarkers, verdict } from "./summary";
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
    [{ psr: 0.97 }, "Statistically convincing: 97% confidence the true Sharpe is above zero"],
    [{ psr: 0.95 }, "Statistically convincing: 95%"],
    [{ psr: 0.94 }, "Promising but not proven: 94%"],
    [{ psr: 0.8 }, "Promising but not proven: 80%"],
    [{ psr: 0.54 }, "Not convincing: only 54% confidence"],
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

  it("marks nothing when there are too many trades to see the equity line", () => {
    const trades = Array.from({ length: MAX_MARKED_TRADES + 1 }, () => trade(1));
    expect(tradeMarkers(run({ style: "brackets", trades }))).toEqual([]);
    expect(tradeMarkers(run({ style: "brackets", trades: trades.slice(1) }))).toHaveLength(2 * MAX_MARKED_TRADES);
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

describe("runs table wording", () => {
  it("says whether a run was a logged trial", () => {
    expect(runRole(run({ research: { slug: "wedge", version: 2 } }))).toBe("trial of wedge v2");
    expect(runRole(run({ strategy_kind: "control" }))).toBe("comparison");
  });

  it("lists parameters", () => {
    expect(paramsText({ swing_bars: 3, target_r: 4 })).toBe("swing_bars=3 target_r=4");
    expect(paramsText({})).toBe("no parameters");
  });

  it("counts round trips for bracket runs and orders for weight runs", () => {
    expect(tradeCount(run({ style: "brackets" }, { trades: 6, round_trips: 3 }))).toBe(3);
    expect(tradeCount(run({}, { trades: 6 }))).toBe(6);
  });
});

describe("the benchmark", () => {
  it("is never flagged for having one trade", () => {
    const bench = run({ strategy: "buy-and-hold" }, { few_trades: true, trades: 1 });
    expect(fewTrades(bench)).toBe(false);
    expect(verdict(bench).trust).toContain("The bar every strategy on BTC has to clear");
    expect(fewTrades(run({}, { few_trades: true }))).toBe(true);
  });
});

describe("defaultDataset", () => {
  // Regression: forms opened on the first dataset (1h), off the 4h horizon notes register.
  it("prefers 4h, then daily", () => {
    const d = (interval: string) => ({ interval });
    expect(defaultDataset([d("1h"), d("4h")])?.interval).toBe("4h");
    expect(defaultDataset([d("1h"), d("1d")])?.interval).toBe("1d");
    expect(defaultDataset([d("1h")])?.interval).toBe("1h");
    expect(defaultDataset([])).toBeUndefined();
  });
});

describe("signalsCaption", () => {
  it("states the range when every bar is drawn", () => {
    expect(signalsCaption("2020-01", "2025-10", 100, 100, 0)).toBe("2020-01 to 2025-10. Research data stops before the holdout.");
  });

  it("says when only the latest bars are drawn", () => {
    const text = signalsCaption("2020-01", "2025-10", 20_000, 51_000, Date.UTC(2023, 7, 1) / 1000);
    expect(text).toContain("the most recent 20,000 of 51,000 bars are drawn (from 2023-08-01)");
  });
});

describe("sorting runs", () => {
  const a = run({ id: "a", created_at: "2026-01-01T00:00:00Z" }, { total_return: 0.2, sharpe: 0.5, max_drawdown: -0.3, trades: 10 }, 400);
  const b = run({ id: "b", created_at: "2026-01-03T00:00:00Z" }, { total_return: -0.1, sharpe: null, max_drawdown: -0.1, trades: 40 }, 400);
  const c = run({ id: "c", created_at: "2026-01-02T00:00:00Z" }, { total_return: 0.5, sharpe: 1.2, max_drawdown: -0.6, trades: 5 }, 400);
  const ids = (key: Parameters<typeof nextSort>[1], dir: "desc" | "asc" = "desc") =>
    sortRuns([a, b, c], { key, dir }).map((r) => r.id);

  it("starts newest first", () => {
    expect(sortRuns([a, b, c], DEFAULT_RUN_SORT).map((r) => r.id)).toEqual(["b", "c", "a"]);
  });

  it("puts the best first, then reverses", () => {
    expect(ids("profit")).toEqual(["c", "a", "b"]);
    expect(ids("profit", "asc")).toEqual(["b", "a", "c"]);
    expect(ids("drawdown")).toEqual(["b", "a", "c"]); // shallowest first
    expect(ids("trades")).toEqual(["b", "a", "c"]);
  });

  it("sorts profit by return, so different starting cash compares fairly", () => {
    const small = run({ id: "small", initial_cash: "100" }, { final_equity: 150, total_return: 0.5 });
    const big = run({ id: "big", initial_cash: "10000" }, { final_equity: 10_100, total_return: 0.01 });
    expect(sortRuns([big, small], { key: "profit", dir: "desc" }).map((r) => r.id)).toEqual(["small", "big"]);
  });

  it("puts runs without a value last in both directions", () => {
    expect(ids("sharpe")).toEqual(["c", "a", "b"]);
    expect(ids("sharpe", "asc")).toEqual(["a", "c", "b"]);
    const bench = run({ id: "bench", strategy: "buy-and-hold" }, { total_return: 9 });
    expect(sortRuns([bench, a], { key: "vsHold", dir: "desc" }).map((r) => r.id)).toEqual(["a", "bench"]);
  });

  it("flips direction on a second click, and starts a new column best first", () => {
    expect(nextSort({ key: "sharpe", dir: "desc" }, "sharpe")).toEqual({ key: "sharpe", dir: "asc" });
    expect(nextSort({ key: "sharpe", dir: "asc" }, "trades")).toEqual({ key: "trades", dir: "desc" });
  });
});

describe("cross-sectional runs", () => {
  const ds = (symbol: string, interval: "1h" | "4h" | "1d") =>
    ({ venue: "binance", symbol, interval, months: 70, rows: 2100, first: "2019-01-01T00:00:00Z", last: "2026-10-31T00:00:00Z" });

  it("are compared with BTC, not with 'UNIVERSE'", () => {
    expect(heldAsset({ symbol: UNIVERSE, account_currency: "USDT", benchmark_symbol: "BTCUSDT" })).toBe("BTC");
    expect(heldAsset({ symbol: "ETHUSDT", account_currency: "USDT", benchmark_symbol: null })).toBe("ETH");
    const v = verdict(run({ symbol: UNIVERSE, benchmark_symbol: "BTCUSDT" }, { final_equity: 800 }, 700));
    expect(v.headline).toBe("Beat holding BTC by 100.00 USDT after costs.");
  });

  it("name their market", () => {
    expect(marketLabel({ symbol: UNIVERSE, interval: "1d", universe_coins: 412 })).toBe("All coins (412) 1d");
    expect(marketLabel({ symbol: UNIVERSE, interval: "1d" })).toBe("All coins 1d");
    expect(marketLabel({ symbol: "BTCUSDT", interval: "4h" })).toBe("BTCUSDT 4h");
  });

  it("run on the whole universe, dated by BTC's daily bars", () => {
    const stored = [ds("BTCUSDT", "4h"), ds("BTCUSDT", "1d"), ds("ETHUSDT", "1d")];
    expect(marketsFor({ universe: true }, stored).map((d) => [d.symbol, d.interval])).toEqual([[UNIVERSE, "1d"]]);
    expect(marketsFor({ universe: true }, [ds("ETHUSDT", "1d")])).toEqual([]); // no BTC daily: can't run
    expect(marketsFor({ universe: false }, stored)).toBe(stored);
    expect(marketsFor(undefined, stored)).toBe(stored);
  });
});

describe("spreadBands", () => {
  it("says the universe spread assumption in words", () => {
    expect(spreadBands([[5e8, 1], [1e8, 3], [2e7, 8], [0, 15]])).toBe(
      "1 bp over 500M a day, 3 bps over 100M a day, 8 bps over 20M a day, else 15 bps",
    );
    expect(spreadBands([[2e9, 1], [0, 5]])).toBe("1 bp over 2B a day, else 5 bps");
  });
});

describe("deflated Sharpe in the verdict", () => {
  it("says how the result looks after every strategy tried", () => {
    const r = run({ deflated_sharpe: 0.04, deflated_trials: 31 }, { psr: 0.97 });
    expect(verdict(r).trust).toBe(
      "Statistically convincing: 97% confidence the true Sharpe is above zero, before correcting for variants tried. After 31 strategies tried: 4% chance it beats the best of them by luck.",
    );
  });

  it("says nothing when there is nothing to correct for", () => {
    expect(deflatedNote({ deflated_sharpe: null, deflated_trials: null })).toBe("");
    expect(deflatedNote({ deflated_sharpe: 0.9, deflated_trials: 1 })).toBe("");
    expect(deflatedNote({})).toBe("");
  });
});

describe("validation studies", () => {
  it("read their verdicts as pass, fail or nothing to test", () => {
    expect(studyTone("ROBUST")).toBe("ok");
    expect(studyTone("SURVIVES")).toBe("ok");
    expect(studyTone("FRAGILE")).toBe("bad");
    expect(studyTone("KILL")).toBe("bad");
    expect(studyTone("NO EDGE")).toBe("neutral");
  });

  it("are titled by kind and strategy", () => {
    expect(studyTitle({ kind: "robustness", strategy: "channel-breakout" })).toBe("Robustness of channel-breakout");
    expect(studyTitle({ kind: "walk-forward", strategy: "ma-trend" })).toBe("Walk-forward of ma-trend");
    expect(studyTitle({ kind: "random-entry", strategy: "structure-pullback" })).toBe(
      "Random-entry control of structure-pullback",
    );
  });
});

describe("decisions", () => {
  it("label pursue, watch and drop", () => {
    expect(DECISION.pursue).toEqual({ tone: "ok", label: "Pursue" });
    expect(DECISION.watch.label).toBe("Watch");
    expect(DECISION.drop.tone).toBe("neutral"); // dropping a failed idea isn't an alarm
  });
});

describe("splitClosed", () => {
  it("keeps the order of both halves", () => {
    const out = splitClosed([1, 2, 3, 4, 5], (n) => n % 2 === 0);
    expect(out).toEqual({ live: [1, 3, 5], closed: [2, 4] });
  });
});

describe("startDateVerdict", () => {
  const row = (window_days: number, windows: number, sharpe: number | null) => ({
    window_days,
    windows,
    sharpe,
    drawdown: 1,
    total_return: 0.31,
  });

  it("reads from the longest window the run is long enough for", () => {
    expect(startDateVerdict([row(365, 55, 0.345), row(730, 43, 0.58)])).toBe(
      "Started on any of 43 dates and held for 2 years, it beat holding on Sharpe from 58% of them, on drawdown from 100% and on return from 31%.",
    );
    expect(startDateVerdict([row(365, 5, 0.2), row(730, 0, null)])).toMatch(/^Started on any of 5 dates and held for 1 year,/);
  });

  it("says nothing when the run is too short for any window", () => {
    expect(startDateVerdict([row(365, 0, null), row(730, 0, null)])).toBeNull();
    expect(startDateVerdict([])).toBeNull();
  });

  it("names windows in years", () => {
    expect(windowLabel(365)).toBe("1 year");
    expect(windowLabel(730)).toBe("2 years");
  });
});

describe("luckBar", () => {
  it("states the Sharpe to beat, or that there is none yet", () => {
    expect(luckBar(72, 1.6712)).toBe("72 trials so far: a backtest Sharpe has to beat about 1.67 before it stands out from luck.");
    expect(luckBar(1, 0)).toMatch(/No trials/);
  });
});

describe("scorecardHeadline", () => {
  const card = (statuses: ("pass" | "fail" | "not run")[], verdict: "pass" | "fail" | "incomplete") => ({
    verdict,
    failed: statuses.filter((s) => s === "fail").length,
    not_run: statuses.filter((s) => s === "not run").length,
    grades: statuses.map((status, i) => ({ name: ["Edge", "Luck", "Out of sample", "Costs"][i]!, rule: "", status, measured: "" })),
  });

  it("names what failed and what is still to run", () => {
    expect(scorecardHeadline(card(["pass", "fail", "not run", "pass"], "fail"))).toEqual({
      tone: "bad",
      text: "Fails one test: luck. Not yet run: out of sample.",
    });
    expect(scorecardHeadline(card(["fail", "fail", "pass", "fail"], "fail")).text).toBe(
      "Fails 3 tests: edge, luck and costs.",
    );
    expect(scorecardHeadline(card(["pass", "not run", "not run", "pass"], "incomplete"))).toEqual({
      tone: "warn",
      text: "Passes every test run so far. Not yet run: luck and out of sample.",
    });
    expect(scorecardHeadline(card(["pass", "pass", "pass", "pass"], "pass")).tone).toBe("ok");
  });
});

describe("datasetsLine", () => {
  it("names the main markets and counts the rest", () => {
    const coins = ["ADAUSDT", "BTCUSDT", "ZECUSDT", "ETHUSDT", "AAVEUSDT", "SOLUSDT"];
    expect(datasetsLine(coins, ["1d", "4h"])).toBe("BTCUSDT, ETHUSDT, SOLUSDT and 3 more at 1d, 4h");
    expect(datasetsLine(["ETHUSDT", "BTCUSDT"], ["1h"])).toBe("BTCUSDT, ETHUSDT at 1h");
    expect(datasetsLine([], [])).toBe("Ingest a dataset to begin");
    const many = Array.from({ length: 1200 }, (_, i) => `C${i}USDT`);
    expect(datasetsLine(many, ["1d"])).toBe("C0USDT, C1000USDT, C1001USDT and 1,197 more at 1d");
  });
});

describe("a scorecard with a test that doesn't apply", () => {
  it("passes when the rest pass", () => {
    const grades = (["pass", "n/a"] as const).map((status) => ({ name: status, rule: "", status, measured: "" }));
    expect(scorecardHeadline({ verdict: "pass", failed: 0, not_run: 0, grades })).toEqual({
      tone: "ok",
      text: "Passes every test that applies.",
    });
  });
});

describe("holdoutEdge", () => {
  const record = (falling: boolean) => ({
    research: "n", version: 1, recorded_at: "", strategy: "trend-vol-target", symbol: "BTCUSDT",
    start: "", end: "", exposure: 0.332, matched_return: -0.0889, falling_year: falling,
    mine: { sharpe: -0.057, total_return: -0.0237, max_drawdown: -0.11 },
    held: { sharpe: 1.2, total_return: -0.29, max_drawdown: -0.47 },
    edge_passes: true, drawdown_passes: true, passes: true,
  });
  it("compares returns at the same exposure in a falling year, Sharpes otherwise", () => {
    expect(holdoutEdge(record(true))).toBe("−2.4% vs −8.9% for holding at its 33% exposure");
    expect(holdoutEdge(record(false))).toBe("Sharpe -0.06 vs 1.20 for holding");
  });
});

describe("cost gate", () => {
  it("colours the verdict", () => {
    expect(gateTone("CARRIES")).toBe("ok");
    expect(gateTone("MARGINAL")).toBe("warn");
    expect(gateTone("FAILS")).toBe("bad");
    expect(gateTone("NO SETUPS")).toBe("neutral");
  });

  it("says never when no win rate covers the costs", () => {
    expect(breakEvenLabel(0.304)).toBe("30%");
    expect(breakEvenLabel(1)).toBe("never");
    expect(breakEvenLabel(1.62)).toBe("never");
    expect(breakEvenLabel(null)).toBe("–");
  });
});

describe("drawdownPaths", () => {
  it("sets the backtest's drawdown beside the reshuffled ones", () => {
    expect(drawdownPaths([-0.412, -0.634], -0.483)).toBe("-48% in the test; median -41%, 1 in 20 worse than -63%");
    expect(drawdownPaths([], -0.4)).toBe("n/a");
  });
});

describe("newestFirst", () => {
  it("keeps the first rows and folds the rest", () => {
    expect(newestFirst([1, 2, 3, 4], 3)).toEqual({ shown: [1, 2, 3], older: [4] });
    expect(newestFirst([1], 3)).toEqual({ shown: [1], older: [] });
  });
});

describe("mostPromising", () => {
  const research = { slug: "a-note", version: 1 };
  const graded = (strategy: string, id: string, sharpe: number, deflated: number, over = {}) =>
    run({ id, strategy, research, deflated_sharpe: deflated, ...over }, { sharpe });

  it("keeps each strategy's best run that beat holding, by deflated Sharpe", () => {
    const runs = [
      graded("a", "a1", 1.5, 0.4),
      graded("a", "a2", 1.6, 0.6), // a's best
      graded("b", "b1", 1.4, 0.5),
      graded("c", "c1", 1.3, 0.3),
      graded("d", "d1", 1.2, 0.2),
      graded("e", "e1", 0.5, 0.9), // below holding: never promising, however lucky
      graded("f", "f1", 2.0, 0.95, { research: null }), // not a research run
      graded("g", "g1", 2.0, 0.95, { used_holdout: true }),
    ];
    const held = { ...runs[0]!.benchmark_metrics, sharpe: 1.0 };
    for (const r of runs) r.benchmark_metrics = held;
    expect(mostPromising(runs).map((r) => r.id)).toEqual(["a2", "b1", "c1"]);
  });
});
