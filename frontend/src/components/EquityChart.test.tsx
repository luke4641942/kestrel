import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EquitySeries } from "../api";
import { ThemeProvider } from "../theme";
import { EquityChart } from "./EquityChart";

// Regression: the chart zoomed back to the whole run on every re-render, because the page
// passed a new markers array each time and every update called fitContent().
const fitContent = vi.fn();

vi.mock("lightweight-charts", () => {
  const series = () => ({ setData: vi.fn(), applyOptions: vi.fn(), priceScale: () => ({ applyOptions: vi.fn() }) });
  return {
    AreaSeries: {},
    LineSeries: {},
    ColorType: { Solid: "solid" },
    LineStyle: { Dashed: 2 },
    PriceScaleMode: { Logarithmic: 1 },
    createSeriesMarkers: () => ({ setMarkers: vi.fn() }),
    createChart: () => ({
      addSeries: series,
      applyOptions: vi.fn(),
      panes: () => [{ setHeight: vi.fn() }, { setHeight: vi.fn() }],
      timeScale: () => ({ fitContent }),
      remove: vi.fn(),
    }),
  };
});

const series = (): EquitySeries => ({ time: [1, 2, 3], equity: [100, 101, 102], drawdown: [0, 0, 0] });

beforeEach(() => fitContent.mockClear());

describe("EquityChart", () => {
  it("zooms to fit new data, but not on a re-render with the same data", () => {
    const equity = series();
    const benchmark = series();
    const chart = (trades: { time: number; side: "buy" | "sell" }[], e = equity) => (
      <ThemeProvider>
        <EquityChart equity={e} benchmark={benchmark} trades={trades} />
      </ThemeProvider>
    );
    const { rerender } = render(chart([]));
    expect(fitContent).toHaveBeenCalledTimes(1);
    rerender(chart([{ time: 2, side: "buy" }])); // a new markers array, same run
    rerender(chart([{ time: 2, side: "buy" }]));
    expect(fitContent).toHaveBeenCalledTimes(1);
    rerender(chart([], series())); // a different run
    expect(fitContent).toHaveBeenCalledTimes(2);
  });
});
