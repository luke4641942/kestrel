import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { App } from "./App";
import { ThemeProvider } from "./theme";

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return { ...real, api: { ...real.api } };
});

beforeEach(() => {
  window.location.hash = "#/trading";
  api.health = vi.fn().mockResolvedValue({ status: "ok", version: "0.1.0" });
  api.jobs = vi.fn().mockResolvedValue([]);
  api.paper = vi.fn().mockResolvedValue([]);
});

describe("navigation", () => {
  it("has Charts as a main tab; reference pages stay under More", async () => {
    render(
      <ThemeProvider>
        <App />
      </ThemeProvider>,
    );
    const nav = screen.getByRole("navigation", { name: "Main" });
    const tabs = within(nav)
      .getAllByRole("link")
      .map((a) => a.textContent);
    expect(tabs).toEqual(["Overview", "Trading", "Research", "Strategies", "Backtests", "Charts"]);
    fireEvent.click(within(nav).getByRole("button", { name: /More/ }));
    const more = screen.getAllByRole("menuitem").map((a) => a.textContent);
    expect(more).toEqual(["What a trade costs", "Rules", "Regimes", "Data"]);
    expect(await screen.findByText("No paper accounts yet")).toBeInTheDocument();
  });
});
