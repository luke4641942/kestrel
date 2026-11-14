import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type Rules as RulesData } from "../api";
import { Rules } from "./Rules";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});

const RULES: RulesData = {
  reviewed: "2026-11-14",
  passing: "A strategy passes only if it beats holding at the same exposure.",
  scorecard: [
    { name: "Edge", rule: "Sharpe beats exposure-matched holding.", why: "Cash is free." },
    { name: "Luck", rule: "Deflated Sharpe at least 95%.", why: "Many tries look lucky." },
  ],
  risk_managed: "Kept because it smooths the ride; not an edge.",
  entry_bar: ["Who pays you and why."],
  account: "At most 3 coins at once.",
  lessons: [{ title: "Trend limits losses.", text: "But holding less does too." }],
};

beforeEach(() => {
  api.rules = vi.fn().mockResolvedValue(RULES);
  api.validation = vi.fn().mockResolvedValue({ trials: 72, sharpe_std: 0.5, expected_max_sharpe: 1.67, studies: [] });
});

describe("Rules", () => {
  it("shows what a pass means, the luck bar, the numbered scorecard and the lessons", async () => {
    render(<Rules />);
    expect(await screen.findByText(RULES.passing)).toBeInTheDocument();
    expect(screen.getByText(/Last reviewed 2026-11-14/)).toBeInTheDocument();
    expect(await screen.findByText(/72 trials so far/)).toBeInTheDocument();
    const tests = [...document.querySelectorAll(".scorecard > li")];
    expect(tests.map((li) => li.querySelector(".scorecard-name")?.textContent)).toEqual(["Edge", "Luck"]);
    expect(screen.getByText("Deflated Sharpe at least 95%.")).toBeInTheDocument();
    expect(screen.getByText("Who pays you and why.")).toBeInTheDocument();
    expect(screen.getByText("At most 3 coins at once.")).toBeInTheDocument();
    expect(screen.getByText("Trend limits losses.")).toBeInTheDocument();
  });

  it("says so when the rules can't be read", async () => {
    api.rules = vi.fn().mockRejectedValue(new Error("findings has no section"));
    render(<Rules />);
    expect(await screen.findByText("Couldn't read the rules")).toBeInTheDocument();
  });
});
