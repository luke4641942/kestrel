import { describe, expect, it } from "vitest";
import type { Bars } from "./api";
import { DemoUnavailable, READ_ONLY, demoFile, demoMeta, demoRequest, sliceBars } from "./demo";

// The same cases are in tests/test_demo.py: both sides must name a call's file alike.
const FILE_CASES: Record<string, string> = {
  "/paper": "paper.json",
  "/backtests/20261122-203214-573b7c": "backtests/20261122-203214-573b7c.json",
  "/strategies/fx%3Atrend/source": "strategies/fx_trend/source.json",
  "/datasets/BTCUSDT/1d/bars?limit=20000&indicators=sma": "datasets/BTCUSDT/1d/bars.json",
  "/validation/history": "validation/history.json",
};

const FULL: Bars = {
  symbol: "BTCUSDT",
  interval: "1d",
  time: [1, 2, 3, 4, 5],
  open: [10, 11, 12, 13, 14],
  high: [10, 11, 12, 13, 14],
  low: [10, 11, 12, 13, 14],
  close: [10, 11, 12, 13, 14],
  volume: [1, 1, 1, 1, 1],
  indicators: { sma: { sma: [null, null, 11, 12, 13] }, rsi: { rsi: [null, 50, 60, 70, 80] } },
};

function serving(files: Record<string, unknown>) {
  const asked: string[] = [];
  const fetcher = async (url: string) => {
    asked.push(url);
    const name = url.replace("/kestrel/demo/", "");
    return name in files
      ? new Response(JSON.stringify(files[name]), { status: 200 })
      : new Response("not found", { status: 404 });
  };
  return { fetcher, asked };
}

describe("the demo's snapshot", () => {
  it("names a call's file as the exporter does", () => {
    for (const [path, name] of Object.entries(FILE_CASES)) expect(demoFile(path)).toBe(name);
  });

  it("slices bars and keeps only the indicators asked for, values unchanged", () => {
    const cut = sliceBars(FULL, 2, ["sma"]);
    expect(cut.time).toEqual([4, 5]);
    expect(cut.close).toEqual([13, 14]);
    expect(cut.indicators).toEqual({ sma: { sma: [12, 13] } });
    expect(sliceBars(FULL, 99, []).time).toHaveLength(5);
  });

  it("answers reads from files under the site's path and refuses writes", async () => {
    const { fetcher, asked } = serving({
      "api/paper.json": [{ name: "tvt" }],
      "api/datasets/BTCUSDT/1d/bars.json": FULL,
    });
    expect(await demoRequest("/paper", undefined, fetcher, "/kestrel/")).toEqual([{ name: "tvt" }]);
    expect(asked).toEqual(["/kestrel/demo/api/paper.json"]);
    const bars = await demoRequest<Bars>(
      "/datasets/BTCUSDT/1d/bars?limit=3&indicators=rsi",
      undefined,
      fetcher,
      "/kestrel/",
    );
    expect(bars.time).toEqual([3, 4, 5]);
    expect(Object.keys(bars.indicators ?? {})).toEqual(["rsi"]);
    await expect(demoRequest("/jobs/backtest", { method: "POST" }, fetcher, "/kestrel/")).rejects.toThrow(
      READ_ONLY,
    );
    await expect(demoRequest("/costs/quote?notional=5", undefined, fetcher, "/kestrel/")).rejects.toBeInstanceOf(
      DemoUnavailable,
    );
  });

  it("reads its own description, or nothing", async () => {
    const meta = { exported_at: "2026-11-24T08:00:00+00:00", commit: "abc1234", markets: [], intervals: [], max_points: 4000 };
    expect(await demoMeta(serving({ "meta.json": meta }).fetcher, "/kestrel/")).toEqual(meta);
    expect(await demoMeta(serving({}).fetcher, "/kestrel/")).toBeUndefined();
  });
});
