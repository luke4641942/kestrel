import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { regimeDetail } from "../regimes.test";
import { Regimes } from "./Regimes";

vi.mock("../api", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api")>();
  return { ...real, api: { ...real.api } };
});
// The chart is lightweight-charts on a canvas; its data comes from tested helpers.
vi.mock("../components/RegimeChart", () => ({ RegimeChart: () => <div data-testid="regime-chart" /> }));

const dataset = (symbol: string, interval = "1d") => ({
  venue: "binance", symbol, interval, months: 99, rows: 3000, first: null, last: null,
});

beforeEach(() => {
  api.datasets = vi.fn().mockResolvedValue([dataset("ETHUSDT"), dataset("BTCUSDT"), dataset("SOLUSDT", "4h")]);
  api.regimes = vi.fn().mockResolvedValue([
    { symbol: "BTCUSDT", created_at: "2026-11-14T00:00:00Z", first: null, last: null, refits: 54 },
  ]);
  api.regime = vi.fn().mockResolvedValue(regimeDetail());
  api.startRegimes = vi.fn().mockResolvedValue({ id: "j1", kind: "regimes" });
});

describe("Regimes", () => {
  it("shows the latest fit: today's reading, a card per state and the switching odds", async () => {
    render(<Regimes jobs={[]} onJobStarted={() => {}} />);
    expect(await screen.findByText(/the model put BTCUSDT in/)).toHaveTextContent("(50% likely)");
    expect(screen.getByTestId("regime-chart")).toBeInTheDocument();
    expect(screen.getByText(/^States from 1970-01-01 to 2025-10-04: 1 monthly fit,/)).toBeInTheDocument();
    const cards = [...document.querySelectorAll(".regime-cards > .panel")];
    expect(cards.map((c) => c.querySelector(".regime-card-title")?.textContent)).toEqual(["Calm", "Normal", "Volatile"]);
    expect(within(cards[2] as HTMLElement).getByText("80% a year")).toBeInTheDocument();
    expect(within(cards[0] as HTMLElement).getByText("+0.10%")).toBeInTheDocument();
    const stays = [...screen.getByRole("table").querySelectorAll(".regime-stay")].map((c) => c.textContent);
    expect(stays).toEqual(["97.0%", "94.0%", "97.0%"]); // the diagonal: staying put
    expect(screen.getByText(/needs its own research note and the scorecard/)).toBeInTheDocument();
  });

  it("offers daily markets only, and fits the chosen one as a job", async () => {
    const started = vi.fn();
    render(<Regimes jobs={[]} onJobStarted={started} />);
    const select = await screen.findByLabelText("Market (daily bars)");
    await waitFor(() => expect([...select.querySelectorAll("option")].map((o) => o.value)).toEqual(["ETHUSDT", "BTCUSDT"]));
    expect(screen.getByRole("button", { name: "Refit" })).toBeInTheDocument(); // BTC is fitted
    fireEvent.change(select, { target: { value: "ETHUSDT" } });
    expect(await screen.findByText("ETHUSDT hasn't been fitted yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Fit regimes" }));
    await waitFor(() => expect(started).toHaveBeenCalled());
    expect(api.startRegimes).toHaveBeenCalledWith("ETHUSDT");
  });

  it("shows why a fit couldn't start", async () => {
    api.startRegimes = vi.fn().mockRejectedValue(new Error("no daily bars for ETHUSDT"));
    render(<Regimes jobs={[]} onJobStarted={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Refit" }));
    expect(await screen.findByText("no daily bars for ETHUSDT")).toBeInTheDocument();
  });
});
