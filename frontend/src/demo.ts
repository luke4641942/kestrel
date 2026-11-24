// The public demo: the dashboard built as a static site (GitHub Pages) that reads a
// snapshot of the API from JSON files instead of a server. The snapshot is written by
// `kestrel demo-export` (src/kestrel/api/demo.py), which names each call's file the same
// way as demoFile below. Writes are refused with a plain message: a static site can't
// run a backtest or freeze a note.
import type { Bars } from "./api";

export const DEMO = import.meta.env.VITE_DEMO === "1";

/** Thrown for anything the demo can't do; pages show its message. */
export class DemoUnavailable extends Error {}

export const READ_ONLY =
  "This is a read-only demo, built from a snapshot. Running backtests, editing notes and " +
  "the research chat need the app running locally.";

/** The snapshot file holding a call: as kestrel.api.demo.file_for. */
export function demoFile(path: string): string {
  const bare = decodeURIComponent(path.split("?")[0]!).replace(/^\/+|\/+$/g, "");
  return bare.replace(/[^A-Za-z0-9._/-]/g, "_") + ".json";
}

const BARS = /^\/datasets\/[^/]+\/[^/]+\/bars\?/;

/** The most recent ``limit`` bars with only the indicators asked for, as the API sends
 * them. The snapshot holds every indicator, computed over the full history before the first
 * bar, so a slice carries the same values the server would compute. */
export function sliceBars(full: Bars, limit: number, indicators: string[]): Bars {
  const from = Math.max(0, full.time.length - limit);
  const cut = <T,>(xs: T[]) => xs.slice(from);
  const lines: NonNullable<Bars["indicators"]> = {};
  for (const key of indicators) {
    const preset = full.indicators?.[key];
    if (!preset) continue;
    lines[key] = Object.fromEntries(Object.entries(preset).map(([name, v]) => [name, cut(v)]));
  }
  return {
    ...full,
    time: cut(full.time),
    open: cut(full.open),
    high: cut(full.high),
    low: cut(full.low),
    close: cut(full.close),
    volume: cut(full.volume),
    indicators: lines,
  };
}

export interface DemoMeta {
  exported_at: string;
  commit: string | null;
  markets: string[];
  intervals: string[];
  max_points: number;
}

type Fetcher = (url: string) => Promise<Response>;

/** A call answered from the snapshot under ``base`` (the site's own path). */
export async function demoRequest<T>(
  path: string,
  init: RequestInit | undefined,
  fetcher: Fetcher = (u) => fetch(u),
  base: string = import.meta.env.BASE_URL,
): Promise<T> {
  if (init?.method && init.method !== "GET") throw new DemoUnavailable(READ_ONLY);
  const res = await fetcher(`${base}demo/api/${demoFile(path)}`);
  if (!res.ok) {
    throw new DemoUnavailable(
      "Not in the demo's snapshot. It carries every research result, backtest and paper " +
        "account, and prices for BTC, ETH and SOL at 1h, 4h and 1d; the rest needs the app " +
        "running locally.",
    );
  }
  const body = (await res.json()) as T;
  if (BARS.test(path)) {
    const params = new URLSearchParams(path.split("?")[1]);
    return sliceBars(body as Bars, Number(params.get("limit") ?? 2000), params.getAll("indicators")) as T;
  }
  return body;
}

/** The snapshot's own description, for the banner. */
export async function demoMeta(
  fetcher: Fetcher = (u) => fetch(u),
  base: string = import.meta.env.BASE_URL,
): Promise<DemoMeta | undefined> {
  try {
    const res = await fetcher(`${base}demo/meta.json`);
    return res.ok ? ((await res.json()) as DemoMeta) : undefined;
  } catch {
    return undefined;
  }
}
