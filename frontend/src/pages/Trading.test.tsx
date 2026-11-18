import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { paperDetail } from "../paperView.test";
import { Trading } from "./Trading";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});
vi.mock("../components/EquityChart", () => ({ EquityChart: () => <div data-testid="equity-chart" /> }));

const summary = (name: string, halted: string | null = null) => {
  const { equity_series, expected, fills, trades, decisions, events, tracking, ...rest } = paperDetail({ name, halted });
  void equity_series, expected, fills, trades, decisions, events, tracking;
  return rest;
};

beforeEach(() => {
  api.paperTotal = vi.fn().mockResolvedValue({
    accounts: 2, initial_cash: 660, equity: 693, expected_equity: 700, profit: 33,
    equity_series: { time: [1, 2], equity: [660, 693], drawdown: [0, 0] },
    expected: { time: [1, 2], equity: [660, 700], drawdown: [0, 0] },
  });
  api.paper = vi.fn().mockResolvedValue([summary("btc"), summary("eth", "killed: drill")]);
  api.paperAccount = vi.fn().mockImplementation((name: string) =>
    Promise.resolve(
      paperDetail({
        name,
        halted: name === "eth" ? "killed: drill" : null,
        trades: [{ symbol: "BTCUSDT", opened: "2026-11-15T00:00:00Z", closed: "2026-11-18T00:00:00Z", bought: 112.08, sold: 111.1, value: null, profit: -0.98, fees: 0, fills: 2 }],
        fills: [{ at: "2026-11-14T00:00:00Z", symbol: "BTCUSDT", side: "buy", quantity: 0.0016, reference_price: 68314, price: 67250, fee: 0, cost_bps: -155.7, expected_bps: 11.3 }],
        decisions: [
          { bar: "2026-11-14T00:00:00Z", target: 0.33, current: 0.33, missed: false, band_skip: true },
          { bar: "2026-11-13T00:00:00Z", target: 0.33, current: 0, missed: true, band_skip: false },
        ],
        events: name === "eth" ? [{ type: "kill", at: "2026-11-14T01:00:00Z", reason: null }, { type: "halt", at: "2026-11-14T01:00:00Z", reason: "killed: drill" }] : [],
      }),
    ),
  );
});

describe("Trading", () => {
  it("lists accounts and shows the first one tracking its model", async () => {
    render(<Trading />);
    expect(await screen.findByText(/Decisions match the model on all 2 days/)).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1, 3);
    expect(within(rows[1]!).getByText("Halted")).toBeInTheDocument();
    expect(screen.getAllByTestId("equity-chart")).toHaveLength(2);  // all accounts, then this one
    expect(screen.getByText("-155.7 bps")).toBeInTheDocument();
    expect(screen.getByText("Trades", { selector: "h3" })).toBeInTheDocument();
    expect(screen.getByText("−0.98")).toBeInTheDocument();  // the trade's profit
    expect(screen.getByText("1 closed (0 won, 1 lost)")).toBeInTheDocument();
    expect(screen.getByText("Missed: the run was late")).toBeInTheDocument();
    expect(screen.getByText(/Testnet fills come from Binance's test order book/)).toBeInTheDocument();
    expect(screen.getByText(/kestrel kill btc --reason/)).toBeInTheDocument();
  });

  it("adds every account up, with net profit and a chart against the model", async () => {
    render(<Trading />);
    expect(await screen.findByText("All accounts")).toBeInTheDocument();
    expect(screen.getByText("+33.00 USDT")).toBeInTheDocument();
    expect(screen.getByText("+5.00% on 660.00 USDT")).toBeInTheDocument();
    expect(screen.getByText("−1.00%")).toBeInTheDocument();
    expect(screen.getByText("Paper accounts")).toBeInTheDocument();
  });

  it("shows why a halted account is halted, and how to resume it", async () => {
    render(<Trading />);
    fireEvent.click(await screen.findByText("eth"));
    expect(await screen.findByRole("alert")).toHaveTextContent('Halted: killed: drill. It won\'t trade until resumed from the terminal: kestrel paper resume eth --reason "..."');
    await waitFor(() => expect(api.paperAccount).toHaveBeenLastCalledWith("eth"));
  });

  it("shows a basket's coins, its fills by coin and its weekly targets", async () => {
    const basket = paperDetail({
      name: "rot",
      symbol: "UNIVERSE",
      positions: { SOLUSDT: 0.5 },
      fills: [{ at: "2026-11-16T00:00:00Z", symbol: "SOLUSDT", side: "buy", quantity: 0.5, reference_price: 140, price: 140.1, fee: 0.05, cost_bps: 7.9, expected_bps: 8.2 }],
      decisions: [{ bar: "2026-11-15T00:00:00Z", target: 0.17, current: null, missed: false, band_skip: false, targets: { SOLUSDT: 0.17 } }],
    });
    const { equity_series, expected, fills, trades, decisions, events, tracking, ...rest } = basket;
    void equity_series, expected, fills, trades, decisions, events, tracking;
    api.paper = vi.fn().mockResolvedValue([rest]);
    api.paperAccount = vi.fn().mockResolvedValue(basket);
    render(<Trading />);
    expect(await screen.findByText("1 coin")).toBeInTheDocument();
    expect(screen.getByText("Basket 1d")).toBeInTheDocument();
    expect(screen.getByText("SOL 17%")).toBeInTheDocument();
    expect(screen.getByText("SOL")).toBeInTheDocument();  // the fill's coin
  });

  it("explains how to open an account when there are none", async () => {
    api.paper = vi.fn().mockResolvedValue([]);
    render(<Trading />);
    expect(await screen.findByText("No paper accounts yet")).toBeInTheDocument();
    expect(screen.getByText(/kestrel paper schedule btc/)).toBeInTheDocument();
  });
});
