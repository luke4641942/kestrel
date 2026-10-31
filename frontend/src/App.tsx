import { useEffect, useState, type ReactNode } from "react";
import { api, type Job } from "./api";
import { JobsDock } from "./components/JobsDock";
import { ErrorBanner, Status } from "./components/ui";
import { isActive } from "./format";
import { useResource } from "./hooks";
import { Costs } from "./pages/Costs";
import { Data } from "./pages/Data";
import { Overview } from "./pages/Overview";
import { Backtests, Trading } from "./pages/Placeholders";
import { useTheme } from "./theme";

export type Route = "overview" | "data" | "costs" | "backtests" | "trading";

const NAV: { route: Route; label: string; later?: string }[] = [
  { route: "overview", label: "Overview" },
  { route: "data", label: "Data" },
  { route: "costs", label: "What a trade costs" },
  { route: "backtests", label: "Backtests", later: "P2" },
  { route: "trading", label: "Trading", later: "P4" },
];

function readRoute(): Route {
  const r = window.location.hash.replace(/^#\/?/, "") as Route;
  return NAV.some((n) => n.route === r) ? r : "overview";
}

function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === "light" ? "dark" : "light";
  return (
    <button className="icon-btn" onClick={toggle} aria-label={`Switch to ${next} theme`} title={`Switch to ${next} theme`}>
      {theme === "light" ? (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
          <path
            d="M13.5 9.6A5.6 5.6 0 0 1 6.4 2.5a5.6 5.6 0 1 0 7.1 7.1Z"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinejoin="round"
          />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
          <circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.4" />
          <path
            d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
          />
        </svg>
      )}
    </button>
  );
}

export function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  const health = useResource(api.health, [], 15_000);

  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Browser tab title follows the page, e.g. "What a trade costs · Kestrel".
  useEffect(() => {
    const label = NAV.find((n) => n.route === route)?.label;
    document.title = label && route !== "overview" ? `${label} · Kestrel` : "Kestrel";
  }, [route]);

  // Poll jobs quickly while any are active, slowly otherwise.
  const [fast, setFast] = useState(false);
  const jobs = useResource(api.jobs, [], fast ? 1000 : 5000);
  const jobList = jobs.data ?? [];
  const active = jobList.filter(isActive).length;
  useEffect(() => setFast(active > 0), [active]);
  const [dockOpen, setDockOpen] = useState(false);

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
      page = <Overview jobs={jobList} go={go} openJobs={() => setDockOpen(true)} />;
      break;
    case "data":
      page = <Data jobs={jobList} onJobStarted={onJobStarted} />;
      break;
    case "costs":
      page = <Costs />;
      break;
    case "backtests":
      page = <Backtests />;
      break;
    case "trading":
      page = <Trading />;
      break;
  }

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="wordmark" href="#/overview">
            <svg width="20" height="20" viewBox="0 0 32 32" aria-hidden>
              <rect width="32" height="32" rx="6" fill="currentColor" />
              <path d="M8 20.5 16 10l8 10.5-8-4z" fill="var(--bg)" />
            </svg>
            Kestrel
          </a>
          <nav className="nav" aria-label="Main">
            {NAV.map((n) => (
              <a key={n.route} href={`#/${n.route}`} aria-current={route === n.route ? "page" : undefined}>
                {n.label}
                {n.later && <span className="later">{n.later}</span>}
              </a>
            ))}
          </nav>
          <div className="topbar-end">
            {health.data ? (
              <Status tone="ok">
                <span className="small secondary">API v{health.data.version}</span>
              </Status>
            ) : (
              <Status tone={health.error ? "bad" : "neutral"}>
                <span className="small secondary">{health.error ? "API offline" : "Connecting"}</span>
              </Status>
            )}
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="main">
        <ErrorBanner error={health.error} />
        {page}
      </main>
      <JobsDock jobs={jobList} open={dockOpen} setOpen={setDockOpen} onChange={jobs.reload} />
    </>
  );
}
