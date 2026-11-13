// Which indicator lines a chart draws, where, and in what colour. Plain logic, kept out of
// components so it can be tested directly; components/indicatorLayer.ts draws the plan.
import type { IndicatorInfo } from "./api";

/** Colour tokens for indicators, by the indicator's place in the menu, so a colour always
 * means the same indicator however many are switched on. */
export const INDICATOR_COLORS = ["--ind-1", "--ind-2", "--ind-3", "--ind-4"];

/** Height of each pane an indicator adds; the chart grows by this much per pane, so the
 * price pane keeps its size. */
export const INDICATOR_PANE_PX = 90;

/** How many panes of their own the selected indicators add. */
export function ownPanes(menu: IndicatorInfo[], selected: string[]): number {
  return menu.filter((m) => m.pane === "own" && selected.includes(m.key)).length;
}

export interface PlanLine {
  key: string;
  line: string;
  /** 0 is the price pane; indicators with their own pane get one each, from `firstOwnPane`. */
  pane: number;
  kind: "line" | "histogram";
  /** A CSS variable name. */
  color: string;
  dashed: boolean;
  title: string;
  /** Reference levels, drawn on the pane's first line only. */
  guides: number[];
  percent: boolean;
}

export function indicatorColor(menu: IndicatorInfo[], key: string): string {
  const i = Math.max(0, menu.findIndex((m) => m.key === key));
  return INDICATOR_COLORS[i % INDICATOR_COLORS.length]!;
}

/** The lines to draw for `selected`, in menu order. Bands and signal lines are dashed,
 * MACD's histogram is a histogram, and guides go on each pane's first line. */
export function indicatorPlan(menu: IndicatorInfo[], selected: string[], firstOwnPane: number): PlanLine[] {
  const chosen = menu.filter((m) => selected.includes(m.key));
  let nextPane = firstOwnPane;
  return chosen.flatMap((m) => {
    const pane = m.pane === "price" ? 0 : nextPane++;
    const color = indicatorColor(menu, m.key);
    return m.lines.map(
      (line, i): PlanLine => ({
        key: m.key,
        line,
        pane,
        kind: line === "histogram" ? "histogram" : "line",
        color: line === "signal" || line === "histogram" ? "--text-3" : color,
        dashed: line === "upper" || line === "lower" || line === "signal",
        title: m.lines.length > 1 ? `${m.label} ${line}` : m.label,
        guides: i === 0 ? m.guides : [],
        percent: m.percent,
      }),
    );
  });
}

/** The selection a page remembers between visits, in this browser only. Unknown keys
 * (an indicator since removed) are dropped. */
export function loadSelection(storageKey: string, menu: IndicatorInfo[] | undefined): string[] {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(storageKey) ?? "[]");
    if (!Array.isArray(saved)) return [];
    const strings = saved.filter((k): k is string => typeof k === "string");
    return menu ? strings.filter((k) => menu.some((m) => m.key === k)) : strings;
  } catch {
    return [];
  }
}

export function saveSelection(storageKey: string, keys: string[]): void {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(keys));
  } catch {
    // Private windows and blocked storage: the selection just isn't remembered.
  }
}

/** Toggle one key, keeping the menu's order. */
export function toggled(selected: string[], key: string, menu: IndicatorInfo[]): string[] {
  const next = selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key];
  return menu.map((m) => m.key).filter((k) => next.includes(k));
}
