import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TourStep } from "../tour";
import { Tour } from "./Tour";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api, backtests: vi.fn().mockResolvedValue([]) } };
});

const STEPS: TourStep[] = [
  { route: "rules", target: "pass", title: "First stop", body: "About passing." },
  { route: "research", target: "missing", title: "Second stop", body: "Nothing to ring here." },
];

beforeEach(() => {
  window.location.hash = "";
  document.body.innerHTML = "";
  window.scrollTo = vi.fn() as typeof window.scrollTo;
  const target = document.createElement("div");
  target.setAttribute("data-tour", "pass");
  document.body.appendChild(target);
});

describe("Tour", () => {
  it("opens each stop's page and moves with the buttons", () => {
    const close = vi.fn();
    render(<Tour steps={STEPS} onClose={close} />);
    expect(screen.getByRole("dialog")).toHaveTextContent("First stop");
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/rules");
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Second stop"); // shown even with nothing to ring
    expect(window.location.hash).toBe("#/research");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("First stop");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("leaves with Esc and moves with the arrow keys", () => {
    const close = vi.fn();
    render(<Tour steps={STEPS} onClose={close} />);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByRole("dialog")).toHaveTextContent("Second stop");
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByRole("dialog")).toHaveTextContent("First stop");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(close).toHaveBeenCalledOnce();
  });
});
