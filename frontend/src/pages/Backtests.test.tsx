import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { Backtests } from "./Backtests";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});

beforeEach(() => {
  api.backtests = vi.fn().mockResolvedValue([]);
  api.datasets = vi.fn().mockResolvedValue([
    {
      venue: "binance",
      symbol: "BTCUSDT",
      interval: "4h",
      months: 60,
      rows: 13_000,
      first: "2021-01-01T00:00:00Z",
      last: "2026-09-30T20:00:00Z",
    },
  ]);
  api.strategies = vi.fn().mockResolvedValue([
    { key: "buy-and-hold", kind: "benchmark", description: "The benchmark.", params: [] },
    { key: "secret", kind: "research", description: "Research code.", params: [] },
  ]);
  api.researchList = vi.fn().mockResolvedValue([
    { slug: "frozen", name: "Frozen idea", status: "specified", created_at: "2026-01-01T00:00:00Z", version: 2, frozen: true, implementation: "buy-and-hold", runs: 0, trials: 0 },
    { slug: "draft", name: "Draft idea", status: "idea", created_at: "2026-01-01T00:00:00Z", version: 1, frozen: false, implementation: "buy-and-hold", runs: 0, trials: 0 },
    { slug: "dead", name: "Rejected idea", status: "rejected", created_at: "2026-01-01T00:00:00Z", version: 1, frozen: true, implementation: "buy-and-hold", runs: 0, trials: 0 },
  ]);
  api.costs = vi.fn().mockResolvedValue({
    account_currency: "USDT",
    fingerprint: "x",
    costs: { taker_fee_bps: 10, half_spread_bps: 1, impact_coef_bps: 100, max_participation: 0.01 },
    backtest: { no_trade_band: 0.02, holdout_start: "2025-11-01T00:00:00Z" },
    instruments: [],
  });
});

describe("New backtest form", () => {
  it("caps the date range before the holdout", async () => {
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    const end = await screen.findByLabelText("To month");
    await waitFor(() => expect(end).toHaveValue("2025-10"));
    expect(end).toHaveAttribute("max", "2025-10");
    expect(screen.getByLabelText("From month")).toHaveValue("2021-01");
    expect(screen.getByText(/reserved for one final test/)).toBeInTheDocument();
  });

  it("offers only frozen, unrejected research notes, and never research code directly", async () => {
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    const select = await screen.findByLabelText("Strategy");
    await waitFor(() => expect(select).toHaveTextContent("Frozen idea v2"));
    expect(select).not.toHaveTextContent("Draft idea");
    expect(select).not.toHaveTextContent("Rejected idea");
    expect(select).not.toHaveTextContent("secret");
    expect(select).toHaveTextContent("buy-and-hold");
  });

  it("says when there are no runs yet", async () => {
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  });
});
