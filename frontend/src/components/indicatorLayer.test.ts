import { describe, expect, it, vi } from "vitest";
import type { IChartApi } from "lightweight-charts";
import { INDICATOR_PANE_PX, type PlanLine } from "../indicators";
import { IndicatorLayer } from "./indicatorLayer";

vi.mock("lightweight-charts", () => ({ LineSeries: "line", HistogramSeries: "histogram", LineStyle: { Solid: 0, Dashed: 2, Dotted: 1 } }));

/** A chart that records series and panes like lightweight-charts does. */
function fakeChart(basePanes: number) {
  const pane = () => ({ setStretchFactor: vi.fn() });
  const panes: { setStretchFactor: ReturnType<typeof vi.fn> }[] = Array.from({ length: basePanes }, pane);
  const series: { kind: string; pane: number; data: unknown[]; lines: number[] }[] = [];
  const chart = {
    addSeries: (kind: string, _opts: unknown, p = 0) => {
      while (panes.length <= p) panes.push(pane());
      const s = { kind, pane: p, data: [] as unknown[], lines: [] as number[] };
      series.push(s);
      return {
        setData: (d: unknown[]) => (s.data = d),
        createPriceLine: (o: { price: number }) => s.lines.push(o.price),
        _s: s,
      };
    },
    removeSeries: (api: { _s: unknown }) => series.splice(series.indexOf(api._s as never), 1),
    removePane: (i: number) => panes.splice(i, 1),
    panes: () => panes,
  };
  return { chart: chart as unknown as IChartApi, series, panes };
}

const line = (over: Partial<PlanLine>): PlanLine => ({
  key: "rsi14",
  line: "value",
  pane: 1,
  kind: "line",
  color: "--ind-1",
  dashed: false,
  title: "RSI 14",
  guides: [],
  percent: false,
  ...over,
});

describe("IndicatorLayer", () => {
  it("draws each line in its pane, with gaps for missing values and guides", () => {
    const f = fakeChart(1);
    const layer = new IndicatorLayer(f.chart, [440]);
    layer.draw(
      [line({ guides: [30, 70] }), line({ key: "sma50", pane: 0 }), line({ key: "macd", line: "histogram", pane: 2, kind: "histogram" })],
      [1, 2],
      { rsi14: { value: [null, 55] }, sma50: { value: [10, 11] }, macd: { histogram: [0.5, -0.5] } },
    );
    expect(f.series.map((s) => [s.kind, s.pane])).toEqual([
      ["line", 1],
      ["line", 0],
      ["histogram", 2],
    ]);
    expect(f.series[0]!.data).toEqual([{ time: 1 }, { time: 1 + 1, value: 55 }]);
    expect(f.series[0]!.lines).toEqual([30, 70]);
    expect(f.panes).toHaveLength(3);
    // Sizes are relative weights in pixels: the price pane keeps 440, each indicator 90.
    expect(f.panes[0]!.setStretchFactor).toHaveBeenCalledWith(440);
    expect(f.panes[1]!.setStretchFactor).toHaveBeenCalledWith(INDICATOR_PANE_PX);
    expect(f.panes[2]!.setStretchFactor).toHaveBeenCalledWith(INDICATOR_PANE_PX);
  });

  it("replaces the previous indicators and removes the panes they added", () => {
    const f = fakeChart(2);
    const layer = new IndicatorLayer(f.chart, [270, 90]);
    layer.draw([line({ pane: 2 }), line({ key: "obv", pane: 3 })], [1], {});
    expect(f.panes).toHaveLength(4);
    layer.draw([line({ key: "sma50", pane: 0 })], [1], undefined);
    expect(f.series).toHaveLength(1);
    expect(f.panes).toHaveLength(2); // the chart's own panes stay
    expect(f.series[0]!.data).toEqual([{ time: 1 }]); // no values sent: all gaps
    layer.clear();
    expect(f.series).toHaveLength(0);
  });
});
