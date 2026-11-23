import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { MarketBadge, MarketNotice, MarketProvider, MarketSwitch } from "./market";

beforeEach(() => localStorage.clear());

describe("the market switch", () => {
  it("chooses a market for every page, remembers it, and labels items only under All", () => {
    const { unmount } = render(
      <MarketProvider>
        <MarketSwitch />
        <MarketNotice route="trading" />
        <MarketBadge cls="forex" />
      </MarketProvider>,
    );
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector(".chip.market-forex")).toHaveTextContent("Forex");
    expect(screen.queryByRole("note")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Forex" }));
    expect(screen.getByRole("button", { name: "Forex" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("note")).toHaveTextContent(/crypto accounts only.*Showing crypto/);
    expect(document.querySelector(".chip")).toBeNull(); // one market on screen: no badges
    unmount();

    render(
      <MarketProvider>
        <MarketSwitch />
      </MarketProvider>,
    );
    expect(screen.getByRole("button", { name: "Forex" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Futures" })).toHaveAttribute("title", expect.stringMatching(/Parked/));
  });
});
