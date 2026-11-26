// The funnel on the Research tab: every trial in a pool through the first tests and the
// luck test, then what was kept. Kept out of the component so it can be tested directly.
import type { Funnel, PoolFunnel } from "./api";
import type { Market } from "./markets";

export interface Stage {
  label: string;
  count: number;
  /** Share of the pool's trials, 0 to 1 (the bar's length). */
  share: number;
  note?: string;
}

export const POOL_LABEL: Record<PoolFunnel["pool"], string> = {
  main: "Crypto",
  confirmation: "Crypto, confirmation runs",
  forex: "Forex",
};

const n = (x: number) => x.toLocaleString("en-AU");

export function stages(p: PoolFunnel): Stage[] {
  const share = (x: number) => (p.trials ? x / p.trials : 0);
  const out: Stage[] = [
    { label: "Tried", count: p.trials, share: 1, note: "every distinct setting, market and period" },
    { label: "Positive after costs", count: p.positive, share: share(p.positive) },
    {
      label: "Beat holding (test 1)",
      count: p.beat_holding,
      share: share(p.beat_holding),
      note:
        p.no_holding > 0
          ? `${n(p.no_holding)} with no holding comparison stored, counted as not beating`
          : undefined,
    },
    {
      label: `Above the luck bar (Sharpe ${p.luck_bar.toFixed(2)})`,
      count: p.above_bar,
      share: share(p.above_bar),
    },
    {
      label: "Cleared the luck test (test 2)",
      count: p.cleared_luck,
      share: share(p.cleared_luck),
      note:
        p.unscored > 0
          ? `${n(p.unscored)} above the bar can't be deflated (no daily returns stored): not counted as passes`
          : "deflated Sharpe at least 95%",
    },
  ];
  if (p.passed_own_test !== null) {
    out.push({
      label: "Passed its frozen note's own test",
      count: p.passed_own_test,
      share: share(p.passed_own_test),
      note: "what judged these: two trials make a luck bar near zero",
    });
  }
  return out;
}

/** The pools a market shows: crypto has its main and confirmation pools. */
export function poolsFor(f: Funnel, market: Market): PoolFunnel[] {
  const wanted: PoolFunnel["pool"][] =
    market === "all" ? ["main", "confirmation", "forex"] : market === "crypto" ? ["main", "confirmation"] : market === "forex" ? ["forex"] : [];
  return f.pools.filter((p) => wanted.includes(p.pool));
}

export function keptLine(f: Funnel): string {
  const d = f.kept.decisions;
  const kinds = ["pursue", "watch", "needs-shorting", "drop"]
    .filter((k) => d[k])
    .map((k) => `${d[k]} ${k}`)
    .join(", ");
  const cleared = f.pools.reduce((s, p) => s + p.cleared_luck, 0);
  return (
    `What was kept, per strategy and market: ${kinds || "nothing decided yet"}. ` +
    `${f.kept.holdouts_passed} of ${f.kept.holdouts} holdout tests passed; ${f.kept.paper_accounts} paper accounts. ` +
    (cleared === 0
      ? "Pursue means carry on to paper, not passed: no trial has cleared the luck test."
      : `${cleared} trial${cleared > 1 ? "s" : ""} cleared the luck test.`) +
    ` ${n(f.all_trials)} trials across all pools.`
  );
}
