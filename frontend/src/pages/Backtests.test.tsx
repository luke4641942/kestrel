import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { run, trade } from "../test/fixtures";
import { Backtests } from "./Backtests";

// The canvas chart can't draw in jsdom; it has no logic of its own to test here.
vi.mock("../components/EquityChart", () => ({ EquityChart: () => null }));

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});

beforeEach(() => {
  api.backtests = vi.fn().mockResolvedValue([]);
  api.shortStudies = vi.fn().mockResolvedValue([]);
  api.eventStudies = vi.fn().mockResolvedValue([]);
  api.signalStudies = vi.fn().mockResolvedValue([]);
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

  it("runs a cross-sectional strategy on all coins, not a chosen market", async () => {
    api.datasets = vi.fn().mockResolvedValue([
      { venue: "binance", symbol: "BTCUSDT", interval: "1d", months: 80, rows: 2400, first: "2019-01-01T00:00:00Z", last: "2026-10-31T00:00:00Z" },
      { venue: "binance", symbol: "ETHUSDT", interval: "4h", months: 80, rows: 14000, first: "2020-01-01T00:00:00Z", last: "2026-10-31T00:00:00Z" },
    ]);
    api.strategies = vi.fn().mockResolvedValue([
      { key: "buy-and-hold", kind: "benchmark", description: "The benchmark.", params: [], universe: false },
      { key: "xs-momentum", kind: "research", description: "Ranks coins.", params: [], universe: true },
    ]);
    api.researchList = vi.fn().mockResolvedValue([
      { slug: "mom", name: "Momentum", status: "specified", created_at: "2026-01-01T00:00:00Z", version: 1, frozen: true, implementation: "xs-momentum", runs: 0, trials: 0 },
    ]);
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    const strategy = await screen.findByLabelText("Strategy");
    await waitFor(() => expect(strategy).toHaveTextContent("Momentum v1"));
    fireEvent.change(strategy, { target: { value: "research:mom" } });
    const market = screen.getByLabelText("Market");
    await waitFor(() => expect(market).toHaveTextContent("All coins 1d"));
    expect(market).not.toHaveTextContent("ETHUSDT");
  });

  it("says when there are no runs yet", async () => {
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  });
});

describe("Runs and a run's detail", () => {
  const runs = Array.from({ length: 12 }, (_, i) =>
    run({ id: `20260101-0000${String(i).padStart(2, "0")}-abcdef`, created_at: `2026-01-01T00:00:${String(59 - i).padStart(2, "0")}Z` }),
  );

  beforeEach(() => {
    window.location.hash = "";
    api.backtests = vi.fn().mockResolvedValue(runs);
    api.backtest = vi.fn().mockResolvedValue(
      run({ style: "brackets", trades: Array.from({ length: 60 }, (_, i) => trade(i % 3 === 0 ? 3 : -1)) }, { round_trips: 60 }),
    );
  });

  it("shows the newest ten runs, with the rest one click away", async () => {
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    expect(await screen.findByText("Showing 10 of 12 runs.")).toBeInTheDocument();
    const table = screen.getAllByRole("table")[0]!;
    expect(within(table).getAllByRole("row")).toHaveLength(11); // header + 10
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(within(table).getAllByRole("row")).toHaveLength(13);
  });

  it("sorts by a column when its heading is clicked", async () => {
    const ranked = [0.1, 0.4, -0.2].map((ret, i) =>
      run({ id: `20260101-00000${i}-abcdef`, strategy: `s${i}`, created_at: `2026-01-0${i + 1}T00:00:00Z` }, { total_return: ret }),
    );
    api.backtests = vi.fn().mockResolvedValue(ranked);
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    const names = async () => {
      const table = (await screen.findAllByRole("table"))[0]!;
      return [...table.querySelectorAll(".run-name")].map((e) => e.textContent);
    };
    await waitFor(async () => expect(await names()).toEqual(["s2", "s1", "s0"])); // newest first
    const heading = screen.getByRole("button", { name: /Net profit/ });
    fireEvent.click(heading);
    expect(await names()).toEqual(["s1", "s0", "s2"]);
    expect(heading.closest("th")).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(heading);
    expect(await names()).toEqual(["s2", "s0", "s1"]);
    expect(heading.closest("th")).toHaveAttribute("aria-sort", "ascending");
  });

  it("puts the detail behind tabs, statistics first", async () => {
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    const stats = await screen.findByRole("tab", { name: "Statistics" });
    expect(stats).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Against buy-and-hold")).toBeInTheDocument();
    expect(screen.queryByText("Trades in R")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Trades (60)" }));
    expect(screen.getByText("Trades in R")).toBeInTheDocument();
    expect(screen.getByText("Showing 50 of 60 trades.")).toBeInTheDocument();
    expect(screen.queryByText("Against buy-and-hold")).not.toBeInTheDocument();
  });

  it("shows the scorecard under the headline, naming what failed", async () => {
    const grade = (name: string, status: "pass" | "fail" | "not run", measured: string) => ({ name, rule: `${name} rule`, status, measured });
    api.backtest = vi.fn().mockResolvedValue(
      run({
        scorecard: {
          verdict: "fail",
          failed: 1,
          not_run: 1,
          grades: [
            grade("Edge", "pass", "Sharpe 1.21 vs 1.07 for holding"),
            grade("Luck", "fail", "Deflated Sharpe 28%"),
            grade("Out of sample", "not run", "No walk-forward on this market and period."),
          ],
        },
      }),
    );
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    expect(await screen.findByText("Fails one test: luck. Not yet run: out of sample.")).toBeInTheDocument();
    const rows = [...document.querySelectorAll(".scorecard-graded > li")];
    expect(rows.map((li) => li.className)).toEqual(["grade-pass", "grade-fail", "grade-not-run"]);
    expect(within(rows[1] as HTMLElement).getByText("Deflated Sharpe 28%")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText("Fail")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "How strategies are judged" })).toHaveAttribute("href", "#/rules");
  });

  it("has no scorecard for runs that aren't graded", async () => {
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    await screen.findByRole("tab", { name: "Statistics" });
    expect(document.querySelector(".scorecard-graded")).toBeNull();
  });

  it("leads with the headline numbers in the agreed order", async () => {
    render(<Backtests jobs={[]} onJobStarted={() => {}} />);
    await screen.findByRole("tab", { name: "Statistics" });
    const labels = [...document.querySelectorAll(".stats-8 .stat-label")].map((e) => e.textContent);
    expect(labels).toEqual([
      "Net profit",
      "Against holding BTC",
      "Sharpe",
      "Max drawdown",
      "Expectancy per trade",
      "Cost per round trip",
      "Trades",
      "Time in the market",
    ]);
  });
});
