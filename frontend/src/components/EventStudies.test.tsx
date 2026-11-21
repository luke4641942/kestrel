import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { eventStudy } from "../eventStudies.test";
import { EventStudies } from "./EventStudies";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});

beforeEach(() => {
  api.eventStudies = vi.fn().mockResolvedValue([
    eventStudy(),
    eventStudy({ name: "token-unlocks", verdict: "KILL", mean: 0.004, events: 122 }),
  ]);
});

describe("EventStudies", () => {
  it("lists the studies closed, and opens one with its stats, path and report", async () => {
    render(<EventStudies />);
    expect(await screen.findByText("Event studies")).toBeInTheDocument();
    expect(screen.getByText("Exit rule")).toBeInTheDocument();
    expect(screen.getByText("Kill")).toBeInTheDocument();
    expect(screen.getByText("−15.2%")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("A coin's first perp listing"));
    expect(screen.getByRole("img", { name: "Average path around the event" })).toBeInTheDocument();
    expect(screen.getByText("Same coins, random days")).toBeInTheDocument();
    expect(screen.getByText(/Verdict: EXIT RULE/)).toBeInTheDocument();
  });

  it("shows nothing when no study has been saved", async () => {
    api.eventStudies = vi.fn().mockResolvedValue([]);
    const { container } = render(<EventStudies />);
    await vi.waitFor(() => expect(api.eventStudies).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
