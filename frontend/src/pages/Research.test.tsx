import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type Decision } from "../api";
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
  api.decisions = vi.fn().mockResolvedValue(DECISIONS);
  api.validation = vi.fn().mockResolvedValue({ trials: 751, sharpe_std: 0.5, expected_max_sharpe: 2.23, studies: [] });
  api.validationHistory = vi.fn().mockResolvedValue([{ at: "2026-11-15T15:46:57Z", trials: 751, luck_bar: 2.23 }]);
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

  it("says so when decisions can't be read", async () => {
    api.decisions = vi.fn().mockRejectedValue(new Error("decisions.toml is malformed"));
    render(<Research />);
    expect(await screen.findByText("decisions.toml is malformed")).toBeInTheDocument();
  });
});
