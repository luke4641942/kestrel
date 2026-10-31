import { api, type Job } from "../api";
import type { Route } from "../App";
import { JobsTable } from "../components/JobsTable";
import { PageHeader, Panel, Section, Stat, Status } from "../components/ui";
import { date, num } from "../format";
import { useResource } from "../hooks";

type PhaseState = "done" | "active" | "planned";

const PHASES: { id: string; name: string; state: PhaseState }[] = [
  { id: "P1", name: "Data infrastructure", state: "done" },
  { id: "P1.5", name: "API and dashboard", state: "done" },
  { id: "P2", name: "Backtester and cost model", state: "active" },
  { id: "P3", name: "Validation", state: "planned" },
  { id: "P4", name: "Paper and live execution", state: "planned" },
  { id: "P5", name: "Perpetuals", state: "planned" },
];

const PHASE_STATUS: Record<PhaseState, { tone: "ok" | "info" | "neutral"; label: string }> = {
  done: { tone: "ok", label: "Done" },
  active: { tone: "info", label: "Next" },
  planned: { tone: "neutral", label: "Planned" },
};

export function Overview({ jobs, reloadJobs, go }: { jobs: Job[]; reloadJobs: () => void; go: (r: Route) => void }) {
  const datasets = useResource(api.datasets, [], 10_000);
  const ds = datasets.data ?? [];
  const totalBars = ds.reduce((s, d) => s + d.rows, 0);
  const symbols = new Set(ds.map((d) => d.symbol)).size;
  const running = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const failed = jobs.filter((j) => j.status === "failed").length;
  const earliest = ds.map((d) => d.first).filter(Boolean).sort()[0];
  const latest = ds.map((d) => d.last).filter(Boolean).sort().at(-1);

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="Stored data, running jobs and build progress."
        actions={
          <button className="btn btn-primary" onClick={() => go("data")}>
            Ingest data
          </button>
        }
      />

      <Panel>
        <div className="stats">
          <Stat label="Datasets" value={num(ds.length)} note={`${symbols} symbol${symbols === 1 ? "" : "s"}`} />
          <Stat label="Bars stored" value={num(totalBars)} note="Across all datasets" />
          <Stat
            label="Coverage"
            value={earliest ? date(earliest).slice(0, 7) : "None"}
            note={earliest ? `to ${date(latest)}` : "Ingest a dataset to begin"}
          />
          <Stat
            label="Jobs running"
            value={num(running)}
            note={failed ? `${failed} failed in this session` : "No failures"}
          />
        </div>
      </Panel>

      <div className="split" style={{ marginTop: 40 }}>
        <Section
          title="Recent jobs"
          actions={
            <button className="link-btn" onClick={() => go("jobs")}>
              View all jobs
            </button>
          }
        >
          <Panel>
            <JobsTable jobs={jobs} onChange={reloadJobs} limit={6} />
          </Panel>
        </Section>

        <Section title="Build progress">
          <Panel>
            <ol className="phases">
              {PHASES.map((p) => (
                <li key={p.id} className={p.state}>
                  <span className="id">{p.id}</span>
                  <span className="name">{p.name}</span>
                  <Status tone={PHASE_STATUS[p.state].tone}>
                    <span className="small secondary">{PHASE_STATUS[p.state].label}</span>
                  </Status>
                </li>
              ))}
            </ol>
          </Panel>
        </Section>
      </div>
    </>
  );
}
