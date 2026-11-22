import type { ReactNode } from "react";
import type { JobStatus } from "../api";

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

export function Section({
  title,
  description,
  actions,
  children,
  tour,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** A name the guided tour highlights this section by (tour.ts). */
  tour?: string;
}) {
  return (
    <section className="section" data-tour={tour}>
      {(title || actions) && (
        <div className="section-header">
          <div>
            {title && <h2>{title}</h2>}
            {description && <p>{description}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Panel({ children, pad, tour }: { children: ReactNode; pad?: boolean; tour?: string }) {
  return (
    <div className={`panel${pad ? " panel-pad" : ""}`} data-tour={tour}>
      {children}
    </div>
  );
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

/** A row of tabs; the caller renders the selected tab's content underneath. */
export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { id: T; label: ReactNode }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={t.id === value}
          className={t.id === value ? "active" : undefined}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export type Tone = "ok" | "bad" | "warn" | "info" | "neutral";

export function Status({ tone = "neutral", pulse, children }: { tone?: Tone; pulse?: boolean; children: ReactNode }) {
  return <span className={`status ${tone}${pulse ? " pulse" : ""}`}>{children}</span>;
}

const JOB_TONE: Record<JobStatus, Tone> = {
  queued: "neutral",
  running: "info",
  succeeded: "ok",
  failed: "bad",
  cancelled: "warn",
};

const JOB_LABEL: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function JobStatus({ status }: { status: JobStatus }) {
  return (
    <Status tone={JOB_TONE[status]} pulse={status === "running"}>
      {JOB_LABEL[status]}
    </Status>
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

export function ErrorBanner({ error }: { error?: string }) {
  if (!error) return null;
  return (
    <div className="banner">
      Can't reach the Kestrel API ({error}). Start it with <span className="mono">kestrel serve</span>.
    </div>
  );
}

/** A count of folded-away items (rejected ideas, dropped strategies) and a button to
 * show or hide them. Renders nothing when there are none. */
export function FoldToggle({
  count,
  noun,
  open,
  onToggle,
}: {
  count: number;
  noun: string;
  open: boolean;
  onToggle: () => void;
}) {
  if (count === 0) return null;
  return (
    <div className="table-more">
      <span className="small secondary">
        {count} {noun}
        {open ? "" : " hidden"}.
      </span>
      <button type="button" className="link-btn" aria-expanded={open} onClick={onToggle}>
        {open ? "Hide" : "Show"}
      </button>
    </div>
  );
}
