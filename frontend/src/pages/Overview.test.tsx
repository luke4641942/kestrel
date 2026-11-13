import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { Overview } from "./Overview";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});

beforeEach(() => {
  api.decisions = vi.fn().mockResolvedValue([
    { strategy: "trend-vol-target", name: "Trend following with volatility targeting", market: "BTCUSDT 1d", decision: "pursue", decided: "2026-11-12", reason: "Held up out of sample.", evidence: [] },
    { strategy: "channel-breakout", name: "Channel breakout", market: "BTCUSDT and ETHUSDT 4h", decision: "drop", decided: "2026-11-12", reason: "Weak.", evidence: [] },
  ]);
  api.researchList = vi.fn().mockResolvedValue([]);
  api.validation = vi.fn().mockResolvedValue({ trials: 0, sharpe_std: 0, expected_max_sharpe: 0, studies: [] });
  api.datasets = vi.fn().mockResolvedValue([]);
});

describe("Overview", () => {
  it("leads with what to pursue, by strategy name, with the reason", async () => {
    render(<Overview jobs={[]} go={() => {}} openJobs={() => {}} />);
    await screen.findByText("Held up out of sample.");
    const items = [...document.querySelectorAll<HTMLElement>(".decision-list > li")];
    expect(items).toHaveLength(1); // the dropped one is folded away
    expect(within(items[0]!).getByText("Pursue")).toBeInTheDocument();
    expect(within(items[0]!).getByText(/Trend following with volatility targeting/)).toBeInTheDocument();
    expect(within(items[0]!).getByText("trend-vol-target")).toBeInTheDocument();
    expect(within(items[0]!).getByText("Held up out of sample.")).toBeInTheDocument();
    expect(screen.queryByText("Kill switch")).not.toBeInTheDocument(); // P4 placeholders are gone
  });

  it("folds dropped strategies away until asked", async () => {
    render(<Overview jobs={[]} go={() => {}} openJobs={() => {}} />);
    await screen.findByText("Held up out of sample.");
    expect(document.querySelectorAll(".decision-list > li")).toHaveLength(1);
    expect(screen.getByText("1 dropped strategy hidden.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    const items = [...document.querySelectorAll<HTMLElement>(".decision-list > li")];
    expect(items).toHaveLength(2);
    expect(within(items[1]!).getByText("Drop")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(document.querySelectorAll(".decision-list > li")).toHaveLength(1);
  });

  it("folds rejected ideas in the research pipeline away until asked", async () => {
    const idea = (slug: string, status: string) => ({
      slug, name: `Idea ${slug}`, status, created_at: "2026-11-01T00:00:00Z", version: 1, frozen: true,
      implementation: "ma-trend", runs: 1, trials: 1, deflated_sharpe: null,
    });
    api.researchList = vi.fn().mockResolvedValue([idea("a", "rejected"), idea("b", "backtested"), idea("c", "rejected")]);
    render(<Overview jobs={[]} go={() => {}} openJobs={() => {}} />);
    await screen.findByText("Idea b");
    expect(screen.queryByText("Idea a")).not.toBeInTheDocument();
    expect(screen.getByText("2 rejected ideas hidden.")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Show" }).at(-1)!);
    expect(screen.getByText("Idea a")).toBeInTheDocument();
    expect(screen.getByText("Idea c")).toBeInTheDocument();
  });

  it("says when the decisions file can't be read", async () => {
    api.decisions = vi.fn().mockRejectedValue(new Error("decision 1: no strategy 'magic'"));
    render(<Overview jobs={[]} go={() => {}} openJobs={() => {}} />);
    expect(await screen.findByText("Couldn't read the decisions")).toBeInTheDocument();
    expect(screen.getByText("decision 1: no strategy 'magic'")).toBeInTheDocument();
  });
});
