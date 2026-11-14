import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, type IndicatorInfo } from "../api";
import { Charts } from "./Charts";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});
// The chart is lightweight-charts on a canvas; the bars it gets are checked here instead.
vi.mock("../components/CandleChart", () => ({ CandleChart: () => <div data-testid="candles" /> }));

const ds = (symbol: string, interval: string) => ({ venue: "binance", symbol, interval, months: 1, rows: 1, first: null, last: null });
const info = (key: string, label: string, pane: "price" | "own", reading: string, guides: number[] = []): IndicatorInfo => ({
  key, label, pane, description: label, lines: ["value"], guides, percent: false, reading,
});
const bars = (symbol: string, interval: string) => ({ symbol, interval, time: [1], open: [1], high: [1], low: [1], close: [1], volume: [1], indicators: {} });

beforeEach(() => {
  api.datasets = vi.fn().mockResolvedValue([ds("BTCUSDT", "1d"), ds("BTCUSDT", "4h"), ds("ETHUSDT", "1d"), ds("ADAUSDT", "1d")]);
  api.indicators = vi.fn().mockResolvedValue([
    info("sma200", "SMA 200", "price", "The average of the last 200 closes."),
    info("rsi14", "RSI 14", "own", "Up-moves against down-moves, 0 to 100.", [30, 70]),
  ]);
  api.bars = vi.fn().mockImplementation((symbol: string, interval: string) => Promise.resolve(bars(symbol, interval)));
});
afterEach(() => window.localStorage.clear());

describe("Charts", () => {
  it("opens on BTC daily over a year and loads that many bars", async () => {
    render(<Charts />);
    expect(await screen.findByTestId("candles")).toBeInTheDocument();
    expect(api.bars).toHaveBeenLastCalledWith("BTCUSDT", "1d", 365, []);
    expect(screen.getByText("No indicators on")).toBeInTheDocument();
  });

  it("explains each indicator that is switched on", async () => {
    render(<Charts />);
    fireEvent.click(await screen.findByRole("button", { name: "RSI 14" }));
    expect(await screen.findByText("Up-moves against down-moves, 0 to 100.")).toBeInTheDocument();
    expect(screen.getByText("Guide lines at 30 and 70.")).toBeInTheDocument();
    expect(screen.queryByText("The average of the last 200 closes.")).not.toBeInTheDocument();
    await waitFor(() => expect(api.bars).toHaveBeenLastCalledWith("BTCUSDT", "1d", 365, ["rsi14"]));
  });

  it("switches market, bar size and range", async () => {
    render(<Charts />);
    await screen.findByTestId("candles");
    fireEvent.change(screen.getByLabelText("Bar size"), { target: { value: "4h" } });
    await waitFor(() => expect(api.bars).toHaveBeenLastCalledWith("BTCUSDT", "4h", 2190, []));
    fireEvent.change(screen.getByLabelText("Market"), { target: { value: "ETHUSDT" } });
    await waitFor(() => expect(api.bars).toHaveBeenLastCalledWith("ETHUSDT", "1d", 365, [])); // ETH has no 4h
    fireEvent.change(screen.getByLabelText("Range"), { target: { value: "all" } });
    await waitFor(() => expect(api.bars).toHaveBeenLastCalledWith("ETHUSDT", "1d", 20_000, []));
    expect(JSON.parse(window.localStorage.getItem("kestrel.charts")!)).toEqual({ symbol: "ETHUSDT", interval: "1d", days: null });
  });

  it("says when there is nothing to chart", async () => {
    api.datasets = vi.fn().mockResolvedValue([]);
    render(<Charts />);
    expect(await screen.findByText("No data yet")).toBeInTheDocument();
  });
});
