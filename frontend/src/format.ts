import type { Job } from "./api";

const nf = new Intl.NumberFormat("en-AU");

export function describeJob(job: Job): string {
  const p = job.params as Record<string, string | null>;
  if (job.kind === "ingest") {
    return `Ingest ${p.symbol?.toUpperCase()} ${p.interval}, ${p.start} to ${p.end ?? "latest"}`;
  }
  if (job.kind === "backtest") {
    return `Backtest ${p.strategy} on ${p.symbol} ${p.interval}, ${p.start} to ${p.end}`;
  }
  return job.kind;
}

export const isActive = (job: Job) => job.status === "running" || job.status === "queued";

export const num = (n: number) => nf.format(n);

export function date(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toISOString().slice(0, 10);
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function duration(start: string | null, end: string | null): string {
  if (!start) return "—";
  const ms = (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime();
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

/** Previous full calendar month as YYYY-MM (Binance publishes monthly files after month end). */
export function lastCompleteMonth(): string {
  const d = new Date();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() - 1);
  return d.toISOString().slice(0, 7);
}

/** The month before the holdout starts: the last month research may use. */
export function lastResearchMonth(holdoutStart: string): string {
  const d = new Date(holdoutStart);
  d.setUTCMonth(d.getUTCMonth() - 1);
  return d.toISOString().slice(0, 7);
}
