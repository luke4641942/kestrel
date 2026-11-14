import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Job } from "./api";
import { JobsDock } from "./components/JobsDock";
import { ErrorBanner, Status } from "./components/ui";
import { isActive } from "./format";
import { useResource } from "./hooks";
import { Costs } from "./pages/Costs";
import { Data } from "./pages/Data";
import { Overview } from "./pages/Overview";
import { Regimes } from "./pages/Regimes";
import { Rules } from "./pages/Rules";
import { Strategies } from "./pages/Strategies";
import { Backtests } from "./pages/Backtests";
import { Trading } from "./pages/Placeholders";
import { useTheme } from "./theme";

export type Route = "overview" | "strategies" | "data" | "costs" | "rules" | "regimes" | "backtests" | "trading";

// Main tabs are about strategies and trading; reference and data pages live under More.
const NAV: { route: Route; label: string; later?: string; more?: boolean }[] = [
  { route: "overview", label: "Overview" },
  { route: "strategies", label: "Strategies" },
  { route: "backtests", label: "Backtests" },
  { route: "trading", label: "Trading", later: "P4" },
  { route: "data", label: "Data", more: true },
  { route: "costs", label: "What a trade costs", more: true },
  { route: "regimes", label: "Regimes", more: true },
  { route: "rules", label: "Rules", more: true },
];

/** The page from `#/<route>` or `#/<route>/<id>`; the id is read by the page itself. */
function readRoute(): Route {
  const r = window.location.hash.replace(/^#\/?/, "").split("/")[0] as Route;
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

/**
 * "More" tab holding secondary pages. It is underlined like an active tab when one of
 * its pages is open. The menu is positioned from the button's screen position because
 * the top bar scrolls sideways on narrow screens, which would clip an absolute menu.
 */
function MoreMenu({ route, items }: { route: Route; items: { route: Route; label: string }[] }) {
  const [anchor, setAnchor] = useState<DOMRect>();
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const open = anchor !== undefined;
  const current = items.some((i) => i.route === route);

  useEffect(() => {
    setAnchor(undefined);
  }, [route]);
  useEffect(() => {
    if (!open) return;
    const close = () => setAnchor(undefined);
    const onPointer = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!menu.current?.contains(t) && !button.current?.contains(t)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close();
        button.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);

  return (
    <>
      <button
        ref={button}
        type="button"
        className={`nav-more${current ? " current" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="nav-more-menu"
        onClick={() => setAnchor(open ? undefined : button.current?.getBoundingClientRect())}
      >
        More
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden>
          <path d="m2 3.5 3 3 3-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {anchor && (
        <div
          ref={menu}
          id="nav-more-menu"
          className="nav-menu"
          role="menu"
          style={{
            top: anchor.bottom + 6,
            // clientWidth is the visible width; innerWidth can be wider on mobile.
            left: Math.max(8, Math.min(anchor.left, document.documentElement.clientWidth - 228)),
          }}
        >
          {items.map((i) => (
            <a
              key={i.route}
              role="menuitem"
              href={`#/${i.route}`}
              aria-current={route === i.route ? "page" : undefined}
            >
              {i.label}
            </a>
          ))}
        </div>
      )}
    </>
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
  useEffect(() => {
    setFast(active > 0);
  }, [active]);
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
    case "regimes":
      page = <Regimes jobs={jobList} onJobStarted={onJobStarted} />;
      break;
    case "rules":
      page = <Rules />;
      break;
    case "strategies":
      page = <Strategies jobs={jobList} onJobStarted={onJobStarted} />;
      break;
    case "backtests":
      page = <Backtests jobs={jobList} onJobStarted={onJobStarted} />;
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
            {NAV.filter((n) => !n.more).map((n) => (
              <a key={n.route} href={`#/${n.route}`} aria-current={route === n.route ? "page" : undefined}>
                {n.label}
                {n.later && <span className="later">{n.later}</span>}
              </a>
            ))}
            <MoreMenu route={route} items={NAV.filter((n) => n.more)} />
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
