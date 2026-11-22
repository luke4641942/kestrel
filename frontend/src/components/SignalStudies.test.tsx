import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { signalStudy } from "../signalStudies.test";
import { SignalStudies } from "./SignalStudies";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});

beforeEach(() => {
  api.signalStudies = vi.fn().mockResolvedValue([
    signalStudy(),
    signalStudy({
      signal: "coinbase-premium",
      title: "US buying on Coinbase",
      beside: { ...signalStudy(), verdict: "PROCEED", control_holds: true },
    }),
  ]);
});

describe("SignalStudies", () => {
  it("lists the studies closed, and opens one with its control and report", async () => {
    render(<SignalStudies />);
    expect(await screen.findByText("Signal studies")).toBeInTheDocument();
    expect(screen.getAllByText("Kill")).toHaveLength(2);
    expect(screen.queryByText(/The control/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("A high variance premium"));
    expect(screen.getByText(/The control: positive after a rising week/)).toBeInTheDocument();
    expect(screen.getByText("After a falling week: +1.46% (100 on, 110 off)")).toBeInTheDocument();
    expect(screen.getByText(/Verdict: KILL/)).toBeInTheDocument();
    expect(screen.queryByText("Beside it, deciding nothing")).not.toBeInTheDocument();
  });

  it("shows a side result apart, as deciding nothing", async () => {
    render(<SignalStudies />);
    fireEvent.click(await screen.findByText("US buying on Coinbase"));
    expect(screen.getByText("Beside it, deciding nothing")).toBeInTheDocument();
    expect(screen.getByText(/can't change the verdict above/)).toBeInTheDocument();
  });

  it("shows nothing when no study has been saved", async () => {
    api.signalStudies = vi.fn().mockResolvedValue([]);
    const { container } = render(<SignalStudies />);
    await vi.waitFor(() => expect(api.signalStudies).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
