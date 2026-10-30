import type { ReactNode } from "react";
import type { JobStatus } from "../api";

export function Card({
  title,
  actions,
  children,
  flush,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-header">
          <h2>{title}</h2>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      {flush ? children : <div className="card-body">{children}</div>}
    </section>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="card stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

type Tone = "ok" | "bad" | "warn" | "info" | "neutral";

export function Pill({ tone = "neutral", pulse, children }: { tone?: Tone; pulse?: boolean; children: ReactNode }) {
  return <span className={`pill ${tone}${pulse ? " running" : ""}`}>{children}</span>;
}

const JOB_TONE: Record<JobStatus, Tone> = {
  queued: "neutral",
  running: "info",
  succeeded: "ok",
  failed: "bad",
  cancelled: "warn",
};

export function JobStatusPill({ status }: { status: JobStatus }) {
  return (
    <Pill tone={JOB_TONE[status]} pulse={status === "running"}>
      {status}
    </Pill>
  );
}

export function Progress({ value, status }: { value: number; status?: JobStatus }) {
  const color = status === "failed" ? "var(--down)" : status === "succeeded" ? "var(--up)" : undefined;
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(value * 100)}>
      <div style={{ width: `${Math.round(value * 100)}%`, background: color }} />
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function ErrorBanner({ error }: { error?: string }) {
  if (!error) return null;
  return <div className="banner">Couldn't reach the Kestrel API: {error}</div>;
}
