import { Fragment, useState } from "react";
import { api, type Job } from "../api";
import { ago, duration } from "../format";
import { Empty, JobStatusPill, Progress } from "./ui";

function describe(job: Job): string {
  const p = job.params as Record<string, string | null>;
  if (job.kind === "ingest") return `${p.symbol?.toUpperCase()} ${p.interval} · ${p.start} → ${p.end ?? "latest"}`;
  return job.kind;
}

export function JobsTable({ jobs, onChange, limit }: { jobs: Job[]; onChange: () => void; limit?: number }) {
  const [open, setOpen] = useState<string>();
  const shown = limit ? jobs.slice(0, limit) : jobs;

  if (!jobs.length) {
    return (
      <Empty title="No jobs yet">
        <p>Operations you start from the dashboard (ingestion now, backtests later) appear here with live progress.</p>
      </Empty>
    );
  }

  return (
    <table className="table">
      <thead>
        <tr>
          <th>Status</th>
          <th>Kind</th>
          <th>Details</th>
          <th>Progress</th>
          <th className="num">Started</th>
          <th className="num">Duration</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {shown.map((job) => {
          const active = job.status === "running" || job.status === "queued";
          return (
            <Fragment key={job.id}>
              <tr className="clickable" onClick={() => setOpen(open === job.id ? undefined : job.id)}>
                <td>
                  <JobStatusPill status={job.status} />
                </td>
                <td className="mono">{job.kind}</td>
                <td>
                  <div>{describe(job)}</div>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {job.error ?? job.message}
                  </div>
                </td>
                <td style={{ width: 140 }}>
                  <Progress value={job.progress} status={job.status} />
                </td>
                <td className="num muted">{ago(job.started_at ?? job.created_at)}</td>
                <td className="num mono">{duration(job.started_at, job.finished_at)}</td>
                <td className="num">
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
                </td>
              </tr>
              {open === job.id && (
                <tr>
                  <td colSpan={7} style={{ padding: 0 }}>
                    <pre className="logs">{job.logs.length ? job.logs.join("\n") : "No log output yet."}</pre>
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
