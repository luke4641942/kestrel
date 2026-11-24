// Which market a page shows (Luke, 2026-11-23): crypto, forex or, parked, futures. Kept
// out of components so it can be tested directly. The market of a decision, note or run
// comes from the API (`asset_class`, read from the implementation's key).
import type { Decision, FxRun, ResearchSummary, Validation } from "./api";
import { FAMILIES, familyOf, type Family } from "./researchView";

export type AssetClass = "crypto" | "forex" | "futures";
export type Market = "all" | AssetClass;

export const MARKETS: { id: Market; label: string; parked?: string }[] = [
  { id: "all", label: "All" },
  { id: "crypto", label: "Crypto" },
  { id: "forex", label: "Forex" },
  { id: "futures", label: "Futures", parked: "Parked: paid data, and contracts too large for the account." },
];

export const ASSET_CLASSES: AssetClass[] = ["crypto", "forex", "futures"];

export const LABEL: Record<AssetClass, string> = { crypto: "Crypto", forex: "Forex", futures: "Futures" };

/** The trial pool a market's results are judged against (kestrel.markets). */
export const POOL: Record<AssetClass, string> = { crypto: "main", forex: "forex", futures: "futures" };

/** An item with a market (missing means crypto, as everything was before forex). */
export function classOf(item: { asset_class?: string }): AssetClass {
  const c = item.asset_class;
  return c === "forex" || c === "futures" ? c : "crypto";
}

export function inMarket(item: { asset_class?: string }, market: Market): boolean {
  return market === "all" || classOf(item) === market;
}

/** Pages built for crypto only, so far. Under another market they say so rather than
 * silently showing crypto. */
const CRYPTO_ONLY: Record<string, string> = {
  trading: "Paper trading runs crypto accounts only; forex trades nothing before a note passes.",
  charts: "Charts read the crypto store; forex candles aren't charted yet.",
  regimes: "Regimes are fitted on crypto markets only.",
  costs: "This page prices a Binance spot trade. Forex costs are the spread and nightly financing (Rules).",
  data: "This page manages the crypto store; forex data comes from kestrel ingest-fx.",
};

/** The notice a page shows under ``market`` (and whether it then falls back to showing
 * crypto), or undefined when the page applies as it is. */
export function marketNotice(
  route: string,
  market: Market,
): { text: string; showingCrypto: boolean } | undefined {
  if (market === "all" || market === "crypto") return undefined;
  const cryptoOnly = CRYPTO_ONLY[route];
  if (market === "futures") {
    const parked = "Futures is parked (paid data; contracts too large for the account): nothing has been tried there.";
    return { text: cryptoOnly ? `${parked} ${cryptoOnly}` : parked, showingCrypto: Boolean(cryptoOnly) };
  }
  return cryptoOnly ? { text: cryptoOnly, showingCrypto: true } : undefined;
}

/** A pool's luck bar as shown in a stat: the annual Sharpe, or "none yet" under 2 trials. */
export function poolBar(v: Validation | undefined, cls: AssetClass): string {
  if (!v) return "–";
  const pool = POOL[cls];
  const trials = v.pools?.[pool] ?? (cls === "crypto" ? v.trials : 0);
  const bar = v.pool_bars?.[pool] ?? (cls === "crypto" ? v.expected_max_sharpe : 0);
  return trials < 2 ? "none yet" : bar.toFixed(2);
}

/** The note beside a pool's luck bar: its own bar and trials, and every pool's total. */
export function poolLine(v: Validation | undefined, cls: AssetClass): string {
  if (!v) return "–";
  const pool = POOL[cls];
  const trials = v.pools?.[pool] ?? (cls === "crypto" ? v.trials : 0);
  const bar = v.pool_bars?.[pool] ?? (cls === "crypto" ? v.expected_max_sharpe : 0);
  const all = v.all_trials ?? v.trials;
  if (trials < 2) return `${trials} trial${trials === 1 ? "" : "s"}, no luck bar yet · ${all} across all pools`;
  return `luck bar ${bar.toFixed(2)} from ${trials} trials · ${all} across all pools`;
}

const RANK: Record<Decision["decision"], number> = { pursue: 0, watch: 1, "needs-shorting": 2, drop: 3 };

/** One family in one market. */
export interface GridCell {
  decisions: Decision[];
  /** The best decision there (pursue over watch over needs-shorting over drop). */
  best?: Decision["decision"];
  /** Notes still open (not settled, frozen or not): work in progress, not results. */
  open: number;
  /** Notes run and rejected with no decision recorded: tried, and failed. */
  rejected: number;
}

export interface GridRow {
  family: Family;
  cells: Record<AssetClass, GridCell>;
}

/** Families down, markets across: what has been tried where, and how it went. A family
 * nobody has tried anywhere is left out; "Other" catches strategies without a family. */
export function marketGrid(decisions: Decision[], notes: ResearchSummary[]): GridRow[] {
  const families = [...FAMILIES, familyOf("")];
  const rows = families.map((family) => {
    const cells = {} as Record<AssetClass, GridCell>;
    for (const cls of ASSET_CLASSES) {
      const own = decisions.filter((d) => classOf(d) === cls && familyOf(d.strategy).key === family.key);
      const decided = new Set(own.map((d) => d.strategy));
      const mine = notes.filter(
        (n) =>
          classOf(n) === cls &&
          n.implementation !== null &&
          familyOf(n.implementation).key === family.key &&
          !decided.has(n.implementation),
      );
      const open = mine.filter((n) => n.status !== "rejected" && n.status !== "validated").length;
      const rejected = mine.filter((n) => n.status === "rejected").length;
      const best = [...own].sort((a, b) => RANK[a.decision] - RANK[b.decision])[0]?.decision;
      cells[cls] = { decisions: own, best, open, rejected };
    }
    return { family, cells };
  });
  return rows.filter((r) =>
    ASSET_CLASSES.some((c) => r.cells[c].decisions.length || r.cells[c].open || r.cells[c].rejected),
  );
}

/** What a grid cell says. */
export function cellText(cell: GridCell): string {
  const parts: string[] = [];
  if (cell.decisions.length) {
    const n = cell.decisions.length;
    parts.push(`${cell.best === "needs-shorting" ? "needs shorting" : cell.best} · ${n} decision${n === 1 ? "" : "s"}`);
  }
  if (cell.rejected) parts.push(`${cell.rejected} rejected`);
  if (cell.open) parts.push(`${cell.open} note${cell.open === 1 ? "" : "s"} open`);
  return parts.join(" · ") || "not tried";
}

/** A forex run's row in the runs table. */
export function fxRunRow(run: FxRun): {
  period: string;
  pairs: string;
  ret: string;
  sharpe: string;
  deflated: string;
  drawdown: string;
  gross: string;
  financing: string;
  windows: string;
} {
  const m = run.metrics;
  const pct = (x: number, signed = true) => `${signed && x > 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  const n = run.symbol.split(",").length;
  return {
    period: `${run.start} to ${run.end}`,
    pairs: `${n} pair${n === 1 ? "" : "s"}`,
    ret: m.wiped_out ? "wiped out" : pct(m.total_return),
    sharpe: Number.isFinite(m.sharpe) ? m.sharpe.toFixed(2) : "–",
    deflated: run.deflated_sharpe === null ? "–" : pct(run.deflated_sharpe, false),
    drawdown: pct(m.max_drawdown),
    gross: `${m.average_gross.toFixed(2)}x`,
    financing: `${m.financing >= 0 ? "+" : ""}${m.financing.toFixed(0)} ${run.account_currency}`,
    windows: `${m.windows_positive} of ${m.windows}`,
  };
}
