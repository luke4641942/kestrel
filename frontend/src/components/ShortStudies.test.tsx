import { fireEvent, render, screen } from "@testing-library/react";
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
  it("lists the studies with nothing open, and opens one when clicked", async () => {
    render(<ShortStudies />);
    expect(await screen.findByText("Hypothetical shorting studies")).toBeInTheDocument();
    expect(screen.queryByTestId("equity-chart")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Cash-and-carry on BTC, ETH and SOL perps"));
    expect(screen.getAllByText("Cash-and-carry on BTC, ETH and SOL perps")).toHaveLength(2);
    expect(screen.getByText("Needs shorting")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByTestId("equity-chart")).toBeInTheDocument();
    expect(screen.getAllByText("A 4% stablecoin yield")).toHaveLength(2);  // the stat and the chart key
    expect(screen.getByText(/Verdict: NEEDS-SHORTING/)).toBeInTheDocument();
    expect(screen.getByText("Net profit, after costs")).toBeInTheDocument();
    expect(screen.getByText("+23.10 USDT")).toBeInTheDocument();  // 330 x 7%
    fireEvent.click(screen.getAllByText("Cash-and-carry on BTC, ETH and SOL perps")[0]!);
    expect(screen.queryByTestId("equity-chart")).not.toBeInTheDocument();  // a second click closes it
  });

  it("shows nothing when no study has been run", async () => {
    api.shortStudies = vi.fn().mockResolvedValue([]);
    const { container } = render(<ShortStudies />);
    await vi.waitFor(() => expect(api.shortStudies).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
