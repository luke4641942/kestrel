import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { shortStudy } from "../shortStudies.test";
import { ShortStudies } from "./ShortStudies";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});
vi.mock("./EquityChart", () => ({ EquityChart: () => <div data-testid="equity-chart" /> }));

beforeEach(() => {
  api.shortStudies = vi.fn().mockResolvedValue([shortStudy(), shortStudy({ name: "long-short-momentum", verdict: "DROP", curves: [] })]);
});

describe("ShortStudies", () => {
  it("lists the studies and shows the first with its tests, curve and report", async () => {
    render(<ShortStudies />);
    expect(await screen.findByText("Hypothetical shorting studies")).toBeInTheDocument();
    expect(screen.getAllByText("Cash-and-carry on BTC, ETH and SOL perps")).toHaveLength(2);
    expect(screen.getByText("Needs shorting")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByTestId("equity-chart")).toBeInTheDocument();
    expect(screen.getByText("A 4% stablecoin yield")).toBeInTheDocument();
    expect(screen.getByText(/Verdict: NEEDS-SHORTING/)).toBeInTheDocument();
  });

  it("shows nothing when no study has been run", async () => {
    api.shortStudies = vi.fn().mockResolvedValue([]);
    const { container } = render(<ShortStudies />);
    await vi.waitFor(() => expect(api.shortStudies).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
