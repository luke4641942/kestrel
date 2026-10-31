import { useEffect, useState } from "react";
import { api, type Job } from "../api";
import { ago, describeJob, duration, isActive } from "../format";
import { JobStatus, Progress, Status } from "./ui";

/**
 * Floating job tray, bottom-right on every page. Collapsed it shows what is
 * running right now; expanded it is the full job list with progress, cancel
 * and logs. Replaces the old Jobs tab so job state is visible from anywhere.
 */
export function JobsDock({
  jobs,
  open,
  setOpen,
  onChange,
}: {
  jobs: Job[];
  open: boolean;
  setOpen: (open: boolean) => void;
  onChange: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  const active = jobs.filter(isActive);
  const showLatest = useRecentlyFinished(jobs[0]);

  if (!open) {
    const compact = !active.length && !showLatest;
    return (
      <button
        className={`dock-pill${compact ? " compact" : ""}`}
        onClick={() => setOpen(true)}
        aria-expanded={false}
        aria-controls="jobs-dock"
      >
        <PillSummary jobs={jobs} active={active} showLatest={showLatest} />
      </button>
    );
  }

  return (
    <section id="jobs-dock" className="dock" aria-label="Jobs">
      <header className="dock-header">
        <h2>Jobs</h2>
        <span className="small secondary">
          {active.length ? `${active.length} running` : `${jobs.length} this session`}
        </span>
        <button className="icon-btn" onClick={() => setOpen(false)} aria-label="Collapse jobs" title="Collapse (Esc)">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </header>
      <div className="dock-body">
        {jobs.length ? (
          <ul className="dock-list">
            {jobs.map((job) => (
              <DockItem key={job.id} job={job} onChange={onChange} />
            ))}
          </ul>
        ) : (
          <p className="dock-empty secondary">
            No jobs yet. Ingests you start appear here with live progress; backtests will run the same way.
          </p>
        )}
      </div>
    </section>
  );
}

/** How long a finished job's result stays in the collapsed pill before it shrinks. */
const RESULT_VISIBLE_MS = 125_000;

/** True while `job` finished less than RESULT_VISIBLE_MS ago; flips to false on its own. */
function useRecentlyFinished(job: Job | undefined): boolean {
  const finishedAt = job?.finished_at ? new Date(job.finished_at).getTime() : undefined;
  const remaining = () => (finishedAt === undefined ? 0 : finishedAt + RESULT_VISIBLE_MS - Date.now());
  const [recent, setRecent] = useState(() => remaining() > 0);

  useEffect(() => {
    const ms = remaining();
    setRecent(ms > 0);
    if (ms <= 0) return;
    const timer = window.setTimeout(() => setRecent(false), ms);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishedAt]);

  return recent;
}

function PillSummary({ jobs, active, showLatest }: { jobs: Job[]; active: Job[]; showLatest: boolean }) {
  const [job] = active;
  if (job && active.length === 1) {
    return (
      <>
        <JobStatus status={job.status} />
        <span className="dock-pill-text">{describeJob(job)}</span>
        <span className="mono small">{Math.round(job.progress * 100)}%</span>
        <span className="dock-pill-bar" style={{ width: `${Math.round(job.progress * 100)}%` }} />
      </>
    );
  }
  if (active.length > 1) {
    const progress = active.reduce((s, j) => s + j.progress, 0) / active.length;
    return (
      <>
        <JobStatus status="running" />
        <span className="dock-pill-text">{active.length} jobs running</span>
        <span className="dock-pill-bar" style={{ width: `${Math.round(progress * 100)}%` }} />
      </>
    );
  }
  const latest = jobs[0];
  if (latest && showLatest) {
    return (
      <>
        <JobStatus status={latest.status} />
        <span className="dock-pill-text secondary">{describeJob(latest)}</span>
      </>
    );
  }
  // Compact: a failure keeps its red dot so it can't scroll out of sight unnoticed.
  return latest?.status === "failed" ? (
    <Status tone="bad">
      <span className="secondary">Jobs</span>
    </Status>
  ) : (
    <span className="secondary">Jobs</span>
  );
}

function JobLogs({ job }: { job: Job }) {
  return (
    <pre className="logs">
      <span className="tertiary">job {job.id}</span>
      {"\n"}
      {job.logs.length ? job.logs.join("\n") : "No log output yet."}
    </pre>
  );
}

function DockItem({ job, onChange }: { job: Job; onChange: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const active = isActive(job);
  return (
    <li className="dock-item">
      <div
        className="dock-item-main"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setExpanded(!expanded))}
      >
        <div className="dock-item-top">
          <JobStatus status={job.status} />
          <span className="small tertiary">
            {ago(job.started_at ?? job.created_at)} · {duration(job.started_at, job.finished_at)}
          </span>
          {active && (
            <button
              className="btn btn-sm btn-danger"
              onClick={(e) => {
                e.stopPropagation();
                api.cancelJob(job.id).then(onChange);
              }}
            >
              Cancel
            </button>
          )}
        </div>
        <div>{describeJob(job)}</div>
        <div className={`cell-sub${job.error ? " bad" : ""}`}>{job.error ?? job.message}</div>
        {active && <Progress value={job.progress} status={job.status} />}
      </div>
      {expanded && <JobLogs job={job} />}
    </li>
  );
}
