import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { Strategies } from "./Strategies";

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
  api.researchList = vi.fn().mockResolvedValue([idea("a", "rejected"), idea("b", "specified"), idea("c", "rejected")]);
  // The workspace for the selected idea never loads: only the list is under test.
  for (const name of ["research", "strategies", "datasets", "researchChat", "strategySource"] as const) {
    if (name in api) (api as unknown as Record<string, unknown>)[name] = vi.fn(pending);
  }
});

describe("Strategies list", () => {
  it("shows live ideas and folds rejected ones away until asked", async () => {
    render(<Strategies jobs={[]} onJobStarted={() => {}} />);
    await screen.findByText("Idea b");
    expect(screen.queryByText("Idea a")).not.toBeInTheDocument();
    expect(screen.getByText("2 rejected ideas hidden.")).toBeInTheDocument();
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
