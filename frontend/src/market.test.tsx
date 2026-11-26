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
    const picker = () => screen.getByRole("combobox", { name: "Market" });
    expect(picker()).toHaveValue("all");
    expect(document.querySelector(".chip.market-forex")).toHaveTextContent("Forex");
    expect(screen.queryByRole("note")).not.toBeInTheDocument();

    fireEvent.change(picker(), { target: { value: "forex" } });
    expect(picker()).toHaveValue("forex");
    expect(screen.getByRole("note")).toHaveTextContent(/crypto accounts only.*Showing crypto/);
    expect(document.querySelector(".chip")).toBeNull(); // one market on screen: no badges
    unmount();

    render(
      <MarketProvider>
        <MarketSwitch />
      </MarketProvider>,
    );
    expect(picker()).toHaveValue("forex"); // remembered
    expect(screen.getByRole("option", { name: "Futures (parked)" })).toBeInTheDocument();
    fireEvent.change(picker(), { target: { value: "futures" } });
    expect(picker().closest("label")).toHaveAttribute("title", expect.stringMatching(/Parked/));
  });
});
