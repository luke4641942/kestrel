import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { IndicatorInfo } from "../api";
import { IndicatorPicker } from "./IndicatorPicker";

const menu: IndicatorInfo[] = [
  { key: "sma200", label: "SMA 200", pane: "price", description: "Long-term trend.", lines: ["value"], guides: [], percent: false },
  { key: "rsi14", label: "RSI 14", pane: "own", description: "0-100.", lines: ["value"], guides: [30, 70], percent: false },
];

describe("IndicatorPicker", () => {
  it("groups indicators by where they are drawn and toggles them", () => {
    const onChange = vi.fn();
    render(<IndicatorPicker menu={menu} selected={["rsi14"]} onChange={onChange} />);
    expect(screen.getByRole("group", { name: "On price" })).toHaveTextContent("SMA 200");
    expect(screen.getByRole("group", { name: "Below" })).toHaveTextContent("RSI 14");
    expect(screen.getByRole("button", { name: "RSI 14" })).toHaveAttribute("aria-pressed", "true");
    const sma = screen.getByRole("button", { name: "SMA 200" });
    expect(sma).toHaveAttribute("aria-pressed", "false");
    expect(sma).toHaveAttribute("title", "Long-term trend.");
    fireEvent.click(sma);
    expect(onChange).toHaveBeenCalledWith(["sma200", "rsi14"]);
  });
});
