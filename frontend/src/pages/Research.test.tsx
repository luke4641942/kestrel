import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type Decision } from "../api";
import { MarketProvider } from "../market";
import { Research } from "./Research";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});
// The chart is lightweight-charts on a canvas; its points come from tested helpers.
vi.mock("../components/LuckChart", () => ({ LuckChart: () => <div data-testid="luck-chart" /> }));

const DECISIONS: Decision[] = [
  {
    strategy: "trend-vol-target",
    name: "Trend following with volatility targeting",
    market: "BTCUSDT 1d",
    decision: "pursue",
    decided: "2026-11-14",
    reason: "Beat holding at the same exposure.",
    evidence: [],
  },
  {
    strategy: "rsi-dip",
    name: "RSI dip-buying",
    market: "ETHUSDT 1d",
    decision: "drop",
    decided: "2026-11-15",
    reason: "Sharpe 0.04 against 0.68.",
    evidence: [],
  },
];

beforeEach(() => {
  localStorage.clear();
  api.researchList = vi.fn().mockResolvedValue([
    {
      slug: "forex-trend-across-the-10-pairs",
      name: "Forex trend across the 10 pairs",
      status: "specified",
      created_at: "2026-11-23T00:00:00Z",
      version: 1,
      frozen: true,
      implementation: "fx:trend",
      runs: 0,
      trials: 0,
      asset_class: "forex",
    },
  ]);
  api.decisions = vi.fn().mockResolvedValue(DECISIONS);
  api.validation = vi.fn().mockResolvedValue({
    trials: 751,
    sharpe_std: 0.5,
    expected_max_sharpe: 2.23,
    studies: [
      {
        id: "20261115-231551-21a19d",
        created_at: "2026-11-15T23:15:51Z",
        kind: "confirmation",
        strategy: "btc-regime-trend",
        symbol: "CONFIRMATION",
        interval: "1d",
        start: "first bar",
        end: "2025-11 (exclusive)",
        looks: [],
        verdict: "PASS",
        confirmation: true,
        detail: {
          note: "confirmation-btc-regime-and-coin-trend",
          coins: 2,
          beats_share: 0.5,
          windows: 4,
          windows_share: 0.75,
          median_sharpe: 0.57,
          per_coin: [{ symbol: "AMPUSDT", sharpe: 0.9, held_sharpe: -0.37, windows: 4, windows_beating: 3 }],
        },
      },
    ],
  });
  api.validationHistory = vi.fn().mockResolvedValue([{ at: "2026-11-15T15:46:57Z", trials: 751, luck_bar: 2.23 }]);
  api.funnel = vi.fn().mockResolvedValue({
    pools: [
      { pool: "main", trials: 880, positive: 778, beat_holding: 318, no_holding: 9, above_bar: 0,
        luck_bar: 2.28, cleared_luck: 0, unscored: 0, passed_own_test: null },
      { pool: "forex", trials: 2, positive: 0, beat_holding: 0, no_holding: 2, above_bar: 0,
        luck_bar: 0.11, cleared_luck: 0, unscored: 0, passed_own_test: null },
    ],
    kept: { decisions: { pursue: 6, drop: 48 }, holdouts: 10, holdouts_passed: 5, paper_accounts: 8 },
    all_trials: 882,
  });
  api.rules = vi.fn().mockResolvedValue({
    reviewed: "2026-11-14",
    passing: "",
    scorecard: [],
    risk_managed: "",
    entry_bar: [],
    account: "",
    lessons: [{ title: "Trend is the one survivor.", text: "On crypto only." }],
  });
});

describe("Research", () => {
  it("shows the totals, the families, the luck bar and the findings", async () => {
    render(<Research />);
    expect(await screen.findByRole("button", { name: "Trend following" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reversals and dips" })).toBeInTheDocument();
    expect(screen.getByText("1 pursue")).toBeInTheDocument();
    expect(await screen.findByText("2.23")).toBeInTheDocument();
    expect(await screen.findByTestId("luck-chart")).toBeInTheDocument();
    expect(screen.getByText(/751 trials so far/)).toBeInTheDocument();
    expect(await screen.findByText("Trend is the one survivor.")).toBeInTheDocument();
  });

  it("shows every trial through the funnel, and that none cleared the luck test", async () => {
    render(<MarketProvider><Research /></MarketProvider>);
    const crypto = await screen.findByRole("list", { name: "Crypto funnel" });
    expect(crypto).toHaveTextContent("Tried880");
    expect(crypto).toHaveTextContent("Beat holding (test 1)318");
    expect(crypto).toHaveTextContent("Cleared the luck test (test 2)0");
    expect(screen.getByRole("list", { name: "Forex funnel" })).toBeInTheDocument();
    expect(screen.getByText(/no trial has cleared the luck test/)).toBeInTheDocument();
  });

  it("opens a family to show each decision and its reason", async () => {
    render(<Research />);
    const family = await screen.findByRole("button", { name: "Reversals and dips" });
    expect(screen.queryByText("Sharpe 0.04 against 0.68.")).not.toBeInTheDocument();
    fireEvent.click(family);
    expect(family).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Sharpe 0.04 against 0.68.")).toBeInTheDocument();
    fireEvent.click(family);
    expect(screen.queryByText("Sharpe 0.04 against 0.68.")).not.toBeInTheDocument();
  });

  it("shows each graduate's confirmation run and opens its coins", async () => {
    render(<Research />);
    const graduate = await screen.findByRole("button", { name: "btc-regime-trend" });
    expect(screen.getByText("confirmation-btc-regime-and-coin-trend")).toBeInTheDocument();
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.getByText("75% of 4")).toBeInTheDocument();
    expect(screen.queryByText("AMPUSDT")).not.toBeInTheDocument();
    fireEvent.click(graduate);
    expect(screen.getByText("AMPUSDT")).toBeInTheDocument();
    expect(screen.getByText("3/4")).toBeInTheDocument();
  });

  it("says so when decisions can't be read", async () => {
    api.decisions = vi.fn().mockRejectedValue(new Error("decisions.toml is malformed"));
    render(<Research />);
    expect(await screen.findByText("decisions.toml is malformed")).toBeInTheDocument();
  });

  it("lays families against markets, and the switch filters the page", async () => {
    render(
      <MarketProvider>
        <Research />
      </MarketProvider>,
    );
    expect(await screen.findByText("1 note open")).toBeInTheDocument();
    expect(screen.getByText("pursue · 1 decision")).toBeInTheDocument();
    expect(screen.getAllByText("not tried").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Forex" }));
    // Under Forex the family table holds forex decisions only: none yet.
    expect(screen.queryByRole("button", { name: "Reversals and dips" })).not.toBeInTheDocument();
    expect(screen.getByText("No decisions yet")).toBeInTheDocument();
  });
});
