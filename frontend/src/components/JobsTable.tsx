import { Fragment, useState } from "react";
import { api, type Job } from "../api";
import { ago, duration } from "../format";
import { Empty, JobStatus, Progress } from "./ui";

function describe(job: Job): string {
  const p = job.params as Record<string, string | null>;
  if (job.kind === "ingest") {
    return `Ingest ${p.symbol?.toUpperCase()} ${p.interval}, ${p.start} to ${p.end ?? "latest"}`;
  }
  return job.kind;
}

export function JobsTable({ jobs, onChange, limit }: { jobs: Job[]; onChange: () => void; limit?: number }) {
  const [open, setOpen] = useState<string>();
  const shown = limit ? jobs.slice(0, limit) : jobs;

  if (!jobs.length) {
    return (
      <Empty title="No jobs yet">
        <p>Ingests you start appear here with live progress. Backtests will run the same way.</p>
      </Empty>
    );
  }

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th style={{ width: 130 }}>Status</th>
            <th>Job</th>
            {!limit && <th style={{ width: 160 }}>Progress</th>}
            <th className="num">Started</th>
            <th className="num">Duration</th>
            {!limit && <th style={{ width: 90 }} />}
          </tr>
        </thead>
        <tbody>
          {shown.map((job) => {
            const active = job.status === "running" || job.status === "queued";
            const expanded = open === job.id;
            return (
              <Fragment key={job.id}>
                <tr
                  className="clickable"
                  onClick={() => setOpen(expanded ? undefined : job.id)}
                  aria-expanded={expanded}
                >
                  <td>
                    <JobStatus status={job.status} />
                  </td>
                  <td>
                    <div>{describe(job)}</div>
                    <div className={`cell-sub${job.error ? " bad" : ""}`}>{job.error ?? job.message}</div>
                  </td>
                  {!limit && (
                    <td>
                      <Progress value={job.progress} status={job.status} />
                    </td>
                  )}
                  <td className="num secondary">{ago(job.started_at ?? job.created_at)}</td>
                  <td className="num">{duration(job.started_at, job.finished_at)}</td>
                  {!limit && (
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
                  )}
                </tr>
                {expanded && (
                  <tr>
                    <td colSpan={limit ? 4 : 6} style={{ padding: 0 }}>
                      <pre className="logs">
                        <span className="tertiary">job {job.id}</span>
                        {"\n"}
                        {job.logs.length ? job.logs.join("\n") : "No log output yet."}
                      </pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
