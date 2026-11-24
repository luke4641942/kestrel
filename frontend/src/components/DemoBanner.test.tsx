import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DemoBanner } from "./DemoBanner";

describe("DemoBanner", () => {
  it("says it is a read-only snapshot, of when, and links the source", async () => {
    const load = async () => ({
      exported_at: "2026-11-24T08:00:00+00:00",
      commit: "abc1234",
      markets: [],
      intervals: [],
      max_points: 4000,
    });
    render(<DemoBanner load={load} />);
    expect(screen.getByText("Read-only demo.")).toBeInTheDocument();
    expect(await screen.findByText("abc1234")).toBeInTheDocument();
    expect(screen.getByRole("note").textContent).toContain("24 Nov 2026");
    expect(screen.getByRole("link", { name: "Source on GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/luke4641942/kestrel",
    );
  });
});
