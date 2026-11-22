// Plain logic behind the Trading page: how a paper account is tracking its model, in one
// line. Kept out of components so it can be tested directly.
import type { Condition, PaperDetail, PaperSummary, PaperTotal, PaperTrade, Waiting } from "./api";
import type { Tone } from "./components/ui";

/** The verdict that matters first: do decisions match the backtest's code, and how far
 * have fills and equity drifted from the model? A decision mismatch is always a bug. */
export function trackingVerdict(d: PaperDetail): { tone: Tone; text: string } {
  const t = d.tracking;
  if (t.days === 0) return { tone: "neutral", text: "Nothing handled yet: the first decision comes after the next close." };
  if (t.decision_mismatches > 0) {
    return {
      tone: "bad",
      text: `${t.decision_mismatches} of ${t.days} decisions differ from the model. The same code decides both, so this is a bug to find before trusting anything else here.`,
    };
  }
  const parts = [`Decisions match the model on all ${t.days} days.`];
  if (t.difference !== null) {
    const pct = (t.difference * 100).toFixed(2);
    parts.push(`Equity is ${t.difference >= 0 ? "+" : ""}${pct}% against the model.`);
  }
  if (t.cost_bps !== null && t.expected_bps !== null) {
    parts.push(`Fills cost ${t.cost_bps.toFixed(1)} bps each against ${t.expected_bps.toFixed(1)} modelled.`);
  }
  if (t.missed > 0) parts.push(`${t.missed} day${t.missed === 1 ? " was" : "s were"} missed (the run was late).`);
  const drift = Math.abs(t.difference ?? 0) > 0.02 || t.missed > 0;
  return { tone: drift ? "warn" : "ok", text: parts.join(" ") };
}

export function venueLabel(venue: string): string {
  return venue === "testnet" ? "Binance testnet" : venue === "sim" ? "Simulated" : venue;
}

const coin = (symbol: string) => symbol.replace(/USDT$/, "");

/** A basket account trades a fixed set of coins rather than one symbol. */
export const isBasket = (d: { symbol: string }) => d.symbol === "UNIVERSE";

/** What an account holds, as the Holding stat shows it: a value and a note. */
export function holdingView(d: PaperSummary): { value: string; note: string } {
  const cash = `Cash ${d.cash.toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT`;
  if (!isBasket(d)) return { value: `${d.position} ${coin(d.symbol)}`, note: cash };
  const held = Object.entries(d.positions).sort(([a], [b]) => a.localeCompare(b));
  if (held.length === 0) return { value: "Cash only", note: cash };
  return {
    value: `${held.length} coin${held.length === 1 ? "" : "s"}`,
    note: `${held.map(([s, q]) => `${q} ${coin(s)}`).join(", ")}; ${cash.toLowerCase()}`,
  };
}

/** A basket decision's targets in a line: "SOL 17%, DOGE 15%", or "Cash" when it holds
 * nothing (BTC's regime is off). A coin kept as held shows as "kept". */
export function targetsText(targets: Record<string, number | null>): string {
  const parts = Object.entries(targets)
    .sort(([, a], [, b]) => (b ?? 0) - (a ?? 0))
    .map(([s, w]) => (w === null ? `${coin(s)} kept` : `${coin(s)} ${(w * 100).toFixed(0)}%`));
  return parts.length ? parts.join(", ") : "Cash";
}

/** The whole paper book in numbers: net profit in USDT and as a share of the starting
 * cash, and how far it is from the model. Null before any account has been marked. */
export function totalView(t: PaperTotal): { profit: number; share: number; model: number | null } | null {
  if (t.equity === null || t.profit === null || t.initial_cash <= 0) return null;
  return {
    profit: t.profit,
    share: t.profit / t.initial_cash,
    model: t.expected_equity === null ? null : t.equity / t.expected_equity - 1,
  };
}

/** An account's profit since it opened: equity less its starting cash. Null before its
 * first mark. */
export function accountProfit(a: PaperSummary): { profit: number; share: number } | null {
  if (a.equity === null) return null;
  const profit = a.equity - a.initial_cash;
  return { profit, share: profit / a.initial_cash };
}

/** Its trades in a line: how many closed, won and lost, how many open, and what the closed
 * ones made. */
export function tradesSummary(trades: PaperTrade[]): { text: string; realised: number } {
  const closed = trades.filter((t) => t.closed !== null);
  const realised = closed.reduce((sum, t) => sum + (t.profit ?? 0), 0);
  const won = closed.filter((t) => (t.profit ?? 0) > 0).length;
  const open = trades.length - closed.length;
  if (trades.length === 0) return { text: "No trades yet", realised: 0 };
  const parts = [`${closed.length} closed (${won} won, ${closed.length - won} lost)`];
  if (open) parts.push(`${open} open`);
  return { text: parts.join(", "), realised };
}

/** A trade's profit as a share of what it spent. */
export const tradeReturn = (t: PaperTrade) => (t.profit === null || t.bought <= 0 ? null : t.profit / t.bought);

const pct1 = (x: number) => `${x > 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;

/** One test in words: what it needs by the next close, or how far it can fall and still hold. */
export function conditionText(c: Condition): string {
  if (c.met) return c.move < 0 ? `holds unless ${coin(c.subject)} falls ${(-c.move * 100).toFixed(1)}%` : "holds";
  return `needs ${coin(c.subject)} ${pct1(c.move)} by the next close`;
}

/** The accounts table's one-line answer to "why is it (not) in the market?". */
export function waitingSummary(w: Waiting | null): string {
  if (!w || w.conditions.length === 0) return "n/a";
  const met = w.conditions.filter((c) => c.met).length;
  const total = w.conditions.length;
  const unmet = w.conditions.filter((c) => !c.met);
  if (w.rule === "any") {
    if (unmet.length === 0) return `All ${total} tests hold: fully in`;
    const nearest = unmet.reduce((a, b) => (Math.abs(b.move) < Math.abs(a.move) ? b : a));
    const head = met ? `${met} of ${total} hold` : "Cash";
    return `${head}; next: ${coin(nearest.subject)} ${pct1(nearest.move)}`;
  }
  if (unmet.length === 0) return w.decides === "every close" ? "All tests hold: in" : `Tests hold; decides on ${w.decides}`;
  return `Cash until ${unmet.map((c) => `${coin(c.subject)} ${pct1(c.move)}`).join(" and ")}`;
}

/** How the tests combine, for the account view. */
export function ruleText(w: Waiting): string {
  const when = w.decides === "every close" ? "Decides at every daily close" : `Decides at ${w.decides} only`;
  const how =
    w.rule === "any"
      ? `each test that holds buys ${w.conditions.length > 1 ? `1/${w.conditions.length}` : "all"} of the position (sized to volatility)`
      : w.conditions.length > 1
        ? "in the market only when every test holds"
        : "in the market only when the test holds";
  const picks = w.would_hold.length ? ` If it bought now: ${w.would_hold.map(coin).join(", ")}.` : "";
  return `${when}; ${how}. Moves are what each close would need by the next close.${picks}`;
}
