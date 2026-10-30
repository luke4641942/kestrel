import { useEffect, useState, type ReactNode } from "react";
import { api, type Job } from "./api";
import { JobsTable } from "./components/JobsTable";
import { Card, ErrorBanner, PageHeader, Pill } from "./components/ui";
import { useResource } from "./hooks";
import { Data } from "./pages/Data";
import { Overview } from "./pages/Overview";
import { Backtests, Live } from "./pages/Placeholders";

export type Route = "overview" | "data" | "jobs" | "backtests" | "live";

const NAV: { section: string; items: { route: Route; label: string; tag?: string }[] }[] = [
  {
    section: "Platform",
    items: [
      { route: "overview", label: "Overview" },
      { route: "data", label: "Data" },
      { route: "jobs", label: "Jobs" },
    ],
  },
  {
    section: "Research",
    items: [{ route: "backtests", label: "Backtests", tag: "P2" }],
  },
  {
    section: "Execution",
    items: [{ route: "live", label: "Trading", tag: "P4" }],
  },
];

function readRoute(): Route {
  const r = window.location.hash.replace(/^#\/?/, "");
  return (["overview", "data", "jobs", "backtests", "live"] as Route[]).includes(r as Route)
    ? (r as Route)
    : "overview";
}

export function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  const health = useResource(api.health, [], 15_000);

  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Poll jobs quickly while any are active, slowly otherwise.
  const [fast, setFast] = useState(false);
  const jobs = useResource(api.jobs, [], fast ? 1000 : 5000);
  const jobList = jobs.data ?? [];
  const active = jobList.filter((j) => j.status === "running" || j.status === "queued").length;
  useEffect(() => setFast(active > 0), [active]);

  const go = (r: Route) => {
    window.location.hash = `/${r}`;
  };
  const onJobStarted = (_: Job) => {
    setFast(true);
    jobs.reload();
  };

  let page: ReactNode;
  switch (route) {
    case "overview":
      page = <Overview jobs={jobList} reloadJobs={jobs.reload} go={go} />;
      break;
    case "data":
      page = <Data jobs={jobList} onJobStarted={onJobStarted} />;
      break;
    case "jobs":
      page = (
        <>
          <PageHeader title="Jobs" subtitle="Every operation run through the platform, with progress and logs." />
          <Card flush>
            <JobsTable jobs={jobList} onChange={jobs.reload} />
          </Card>
        </>
      );
      break;
    case "backtests":
      page = <Backtests />;
      break;
    case "live":
      page = <Live />;
      break;
  }

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
            <rect width="32" height="32" rx="7" fill="#1b1f27" />
            <path d="M7 21 L16 9 L25 21 L16 16 Z" fill="#e8a33d" />
          </svg>
          <div>
            Kestrel
            <small>Systematic trading</small>
          </div>
        </div>
        {NAV.map((s) => (
          <div key={s.section}>
            <div className="nav-section">{s.section}</div>
            {s.items.map((i) => (
              <button
                key={i.route}
                className={`nav-item${route === i.route ? " active" : ""}`}
                onClick={() => go(i.route)}
              >
                {i.label}
                {i.route === "jobs" && active > 0 ? (
                  <Pill tone="info" pulse>
                    {active}
                  </Pill>
                ) : (
                  i.tag && <span className="tag">{i.tag}</span>
                )}
              </button>
            ))}
          </div>
        ))}
        <div className="sidebar-footer">
          {health.data ? (
            <>
              <Pill tone="ok">API</Pill>
              <span className="mono">v{health.data.version}</span>
            </>
          ) : (
            <Pill tone={health.error ? "bad" : "neutral"}>{health.error ? "API offline" : "connecting"}</Pill>
          )}
        </div>
      </nav>
      <main className="main">
        <ErrorBanner error={health.error} />
        {page}
      </main>
    </div>
  );
}
