import { afterEach, describe, expect, it } from "vitest";
import type { Dataset, Interval } from "./api";
import { MAX_BARS, barsFor, intervalsFor, loadChoice, marketOptions, saveChoice, switchMarket } from "./charts";

const ds = (symbol: string, interval: Interval): Dataset => ({
  venue: "binance", symbol, interval, months: 1, rows: 1, first: null, last: null,
});
const LIST = [ds("ZECUSDT", "1d"), ds("ETHUSDT", "4h"), ds("BTCUSDT", "1h"), ds("BTCUSDT", "1d"), ds("ADAUSDT", "1d"), ds("ETHUSDT", "1d")];

afterEach(() => window.localStorage.clear());

describe("charts", () => {
  it("offers the main markets first, then the rest alphabetically", () => {
    expect(marketOptions(LIST)).toEqual({ main: ["BTCUSDT", "ETHUSDT"], rest: ["ADAUSDT", "ZECUSDT"] });
    expect(intervalsFor(LIST, "BTCUSDT")).toEqual(["1h", "1d"]);
    expect(intervalsFor(LIST, "SOLUSDT")).toEqual([]);
  });

  it("turns a range into bars, within the cap", () => {
    expect(barsFor("1d", 365)).toBe(365);
    expect(barsFor("4h", 91)).toBe(546);
    expect(barsFor("1h", 3 * 365)).toBe(MAX_BARS); // 26,280 hourly bars is over the cap
    expect(barsFor("1d", null)).toBe(MAX_BARS);
  });

  it("starts on BTC daily over a year, then remembers the last choice if it still exists", () => {
    expect(loadChoice(LIST)).toEqual({ symbol: "BTCUSDT", interval: "1d", days: 365 });
    saveChoice({ symbol: "ETHUSDT", interval: "4h", days: null });
    expect(loadChoice(LIST)).toEqual({ symbol: "ETHUSDT", interval: "4h", days: null });
    saveChoice({ symbol: "SOLUSDT", interval: "4h", days: 91 }); // no longer stored
    expect(loadChoice(LIST)?.symbol).toBe("BTCUSDT");
    window.localStorage.setItem("kestrel.charts", "not json");
    expect(loadChoice(LIST)?.symbol).toBe("BTCUSDT");
    expect(loadChoice([])).toBeUndefined();
  });

  it("keeps the bar size when switching market if it can", () => {
    const now = { symbol: "BTCUSDT", interval: "1h" as Interval, days: 91 };
    expect(switchMarket(LIST, now, "ETHUSDT")).toEqual({ symbol: "ETHUSDT", interval: "1d", days: 91 });
    expect(switchMarket(LIST, { ...now, interval: "4h" }, "ETHUSDT").interval).toBe("4h");
    expect(switchMarket(LIST, { ...now, interval: "4h" }, "ZECUSDT").interval).toBe("1d");
  });
});
