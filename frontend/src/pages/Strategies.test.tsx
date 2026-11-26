import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { Strategies, WorkedExamples } from "./Strategies";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});

const idea = (slug: string, status: string) => ({
  slug,
  name: `Idea ${slug}`,
  status,
  created_at: "2026-11-01T00:00:00Z",
  version: 1,
  frozen: true,
  implementation: "ma-trend",
  runs: 1,
  trials: 1,
  deflated_sharpe: null,
});

const pending = () => new Promise<never>(() => {});

beforeEach(() => {
  window.location.hash = "/strategies";
  api.researchList = vi.fn().mockResolvedValue([idea("a", "rejected"), idea("b", "specified"), idea("c", "validated")]);
  // The workspace for the selected idea never loads: only the list is under test.
  for (const name of ["research", "strategies", "datasets", "researchChat", "strategySource"] as const) {
    if (name in api) (api as unknown as Record<string, unknown>)[name] = vi.fn(pending);
  }
});

describe("Strategies list", () => {
  it("shows live ideas and folds rejected and validated ones away until asked", async () => {
    render(<Strategies jobs={[]} onJobStarted={() => {}} />);
    await screen.findByText("Idea b");
    expect(screen.queryByText("Idea a")).not.toBeInTheDocument();
    expect(screen.getByText("2 rejected or validated ideas hidden.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(screen.getByText("Idea a")).toBeInTheDocument();
    expect(screen.getByText("Idea c")).toBeInTheDocument();
  });

  it("keeps a rejected idea that is open visible in the list", async () => {
    window.location.hash = "/strategies/c";
    render(<Strategies jobs={[]} onJobStarted={() => {}} />);
    const item = await screen.findByText("Idea c");
    expect(item.closest("button")).toHaveClass("active");
    expect(screen.getByRole("button", { name: "Hide" })).toHaveAttribute("aria-expanded", "true");
  });
});

describe("WorkedExamples", () => {
  const example = {
    open_time: "2020-07-04T00:00:00Z",
    kind: "entry" as const,
    before: 0,
    after: 0.49,
    close: 2.779,
    rule: "any" as const,
    conditions: [{ label: "ATOM up over 20 days", subject: "ATOMUSDT", value: 2.779, level: 2.756, met: true, move: 0 }],
    agrees: true,
  };

  it("draws examples on request, then others with the next seed", async () => {
    api.examples = vi.fn().mockResolvedValue([example]);
    render(<WorkedExamples implKey="trend-vol-target" symbol="ATOMUSDT" interval="1d" params={{}} />);
    expect(api.examples).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Draw three" }));
    expect(await screen.findByText("Entry at the close of 2020-07-04 00:00 UTC")).toBeInTheDocument();
    expect(screen.getByText(/ATOM up over 20 days: ATOMUSDT 2.779 against 2.756/)).toBeInTheDocument();
    expect(screen.getByText("Tests and weight agree")).toBeInTheDocument();
    expect(api.examples).toHaveBeenLastCalledWith("trend-vol-target", { symbol: "ATOMUSDT", interval: "1d", params: {}, seed: 0 });
    fireEvent.click(screen.getByRole("button", { name: "Draw three others" }));
    await screen.findByRole("button", { name: "Draw three others" });
    expect(api.examples).toHaveBeenLastCalledWith("trend-vol-target", { symbol: "ATOMUSDT", interval: "1d", params: {}, seed: 1 });
  });

  it("shows the refusal for a coin outside the exploration half", async () => {
    api.examples = vi.fn().mockRejectedValue(new Error("worked examples use exploration-half coins only"));
    render(<WorkedExamples implKey="trend-vol-target" symbol="BTCUSDT" interval="1d" params={{}} />);
    fireEvent.click(screen.getByRole("button", { name: "Draw three" }));
    expect(await screen.findByText(/exploration-half coins only/)).toBeInTheDocument();
  });
});
