import { afterEach, describe, expect, it } from "vitest";
import type { IndicatorInfo } from "./api";
import { indicatorColor, indicatorPlan, loadSelection, ownPanes, saveSelection, toggled } from "./indicators";

const info = (key: string, pane: "price" | "own", lines = ["value"], guides: number[] = []): IndicatorInfo => ({
  key,
  label: key.toUpperCase(),
  pane,
  description: "",
  lines,
  guides,
  percent: false,
  reading: "",
});

const MENU = [
  info("sma50", "price"),
  info("bollinger", "price", ["upper", "middle", "lower"]),
  info("rsi14", "own", ["value"], [30, 70]),
  info("macd", "own", ["macd", "signal", "histogram"], [0]),
  info("obv", "own"),
];

describe("indicatorPlan", () => {
  it("puts overlays on the price pane and gives each other indicator its own pane", () => {
    const plan = indicatorPlan(MENU, ["obv", "sma50", "rsi14"], 2);
    expect(plan.map((p) => [p.key, p.pane])).toEqual([
      ["sma50", 0],
      ["rsi14", 2],
      ["obv", 3],
    ]);
  });

  it("dashes bands and signal lines, draws MACD's histogram as one, and guides each pane once", () => {
    const plan = indicatorPlan(MENU, ["bollinger", "macd"], 1);
    const by = Object.fromEntries(plan.map((p) => [`${p.key}.${p.line}`, p]));
    expect(by["bollinger.upper"]!.dashed).toBe(true);
    expect(by["bollinger.middle"]!.dashed).toBe(false);
    expect(by["macd.histogram"]!.kind).toBe("histogram");
    expect(by["macd.signal"]!.color).toBe("--text-3");
    expect(by["macd.macd"]!.guides).toEqual([0]);
    expect(by["macd.signal"]!.guides).toEqual([]);
    expect(by["bollinger.upper"]!.title).toBe("BOLLINGER upper");
  });

  it("keeps an indicator's colour whatever else is switched on", () => {
    const alone = indicatorPlan(MENU, ["rsi14"], 1)[0]!.color;
    const withOthers = indicatorPlan(MENU, ["sma50", "bollinger", "rsi14"], 1).find((p) => p.key === "rsi14")!.color;
    expect(alone).toBe(withOthers);
    expect(indicatorColor(MENU, "sma50")).toBe("--ind-1");
    expect(indicatorColor(MENU, "obv")).toBe("--ind-1"); // the fifth wraps round
  });
});

describe("ownPanes", () => {
  it("counts only indicators drawn below the price", () => {
    expect(ownPanes(MENU, ["sma50", "bollinger"])).toBe(0);
    expect(ownPanes(MENU, ["sma50", "rsi14", "macd"])).toBe(2);
  });
});

describe("selection", () => {
  afterEach(() => window.localStorage.clear());

  it("is remembered, and drops keys the menu no longer has", () => {
    saveSelection("k", ["rsi14", "gone"]);
    expect(loadSelection("k", undefined)).toEqual(["rsi14", "gone"]);
    expect(loadSelection("k", MENU)).toEqual(["rsi14"]);
  });

  it("survives a corrupt or missing entry", () => {
    expect(loadSelection("missing", MENU)).toEqual([]);
    window.localStorage.setItem("k", "{not json");
    expect(loadSelection("k", MENU)).toEqual([]);
    window.localStorage.setItem("k", '{"a": 1}');
    expect(loadSelection("k", MENU)).toEqual([]);
  });

  it("toggles in menu order", () => {
    expect(toggled(["rsi14"], "sma50", MENU)).toEqual(["sma50", "rsi14"]);
    expect(toggled(["sma50", "rsi14"], "sma50", MENU)).toEqual(["rsi14"]);
  });
});
