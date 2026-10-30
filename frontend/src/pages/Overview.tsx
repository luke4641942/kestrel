import { api, type Job } from "../api";
import { Card, PageHeader, Pill, Stat } from "../components/ui";
import { JobsTable } from "../components/JobsTable";
import { date, num } from "../format";
import { useResource } from "../hooks";
import type { Route } from "../App";

const ROADMAP: { phase: string; name: string; status: "done" | "active" | "planned" }[] = [
  { phase: "P1", name: "Data infrastructure", status: "done" },
  { phase: "P1.5", name: "API & dashboard", status: "active" },
  { phase: "P2", name: "Backtester & cost model", status: "planned" },
  { phase: "P3", name: "Validation (walk-forward, trial log)", status: "planned" },
  { phase: "P4", name: "Execution, paper & live trading", status: "planned" },
  { phase: "P5", name: "Perpetuals", status: "planned" },
];

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
        subtitle="Platform status at a glance."
        actions={
          <button className="btn btn-primary" onClick={() => go("data")}>
            Ingest data
          </button>
        }
      />

      <div className="grid stats">
        <Stat label="Datasets" value={num(ds.length)} sub={`${symbols} symbol${symbols === 1 ? "" : "s"}`} />
        <Stat label="Bars stored" value={num(totalBars)} sub={earliest ? `${date(earliest)} → ${date(latest)}` : "No data yet"} />
        <Stat label="Active jobs" value={num(running)} sub={failed ? `${failed} failed recently` : "No failures"} />
        <Stat label="Strategies live" value="0" sub="Execution arrives in P4" />
      </div>

      <div className="spacer" />

      <div className="grid two-col">
        <Card
          title="Recent jobs"
          flush
          actions={
            <button className="btn btn-sm" onClick={() => go("jobs")}>
              View all
            </button>
          }
        >
          <JobsTable jobs={jobs} onChange={reloadJobs} limit={5} />
        </Card>

        <Card title="Roadmap" flush>
          <ul className="roadmap">
            {ROADMAP.map((r) => (
              <li key={r.phase}>
                <span>
                  <span className="phase">{r.phase}</span>
                  {r.name}
                </span>
                <Pill tone={r.status === "done" ? "ok" : r.status === "active" ? "warn" : "neutral"}>
                  {r.status === "done" ? "done" : r.status === "active" ? "in progress" : "planned"}
                </Pill>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
