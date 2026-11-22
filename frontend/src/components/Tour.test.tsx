import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TourStep } from "../tour";
import { Tour } from "./Tour";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api, backtests: vi.fn().mockResolvedValue([]) } };
});

const STEPS: TourStep[] = [
  { chapter: "Judging", route: "rules", target: "pass", title: "First stop", body: "About passing." },
  { chapter: "Evidence", route: "research", target: "missing", title: "Second stop", body: "Nothing to ring here." },
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
    expect(screen.getByText(/1 of 2/)).toBeInTheDocument();
    expect(screen.getByText(/Judging/)).toBeInTheDocument();
    expect(window.location.hash).toBe("#/rules");
    expect(screen.getByRole("button", { name: "Back" })).toBeDisabled(); // keeps its place
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Second stop"); // shown even with nothing to ring
    expect(window.location.hash).toBe("#/research");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("First stop");
    fireEvent.click(screen.getByRole("button", { name: "Step 2: Second stop" })); // the progress bar jumps
    expect(screen.getByRole("dialog")).toHaveTextContent("Second stop");
    expect(screen.getByRole("button", { name: "Step 2: Second stop" })).toHaveAttribute("aria-current", "step");
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("leaves with the close button", () => {
    const close = vi.fn();
    render(<Tour steps={STEPS} onClose={close} />);
    fireEvent.click(screen.getByRole("button", { name: "Exit the tour" }));
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
