import { useState } from "react";
import { api, type Job } from "../api";
import type { Route } from "../App";
import { Empty, FoldToggle, PageHeader, Panel, Section, Status } from "../components/ui";
import { DECISION, RESEARCH_STATUS, datasetsLine, holdoutEdge, marketLabel, isSettled, newestFirst, splitClosed, studyTitle, studyTone } from "../summary";
import { ago, date, num } from "../format";
import { useResource } from "../hooks";

type PhaseState = "done" | "active" | "planned" | "parked";

// Parked items are agreed ideas waiting on an earlier phase; `id` says which.
const PHASES: { id: string; name: string; state: PhaseState }[] = [
  { id: "P1", name: "Data infrastructure", state: "done" },
  { id: "P1.5", name: "API and dashboard", state: "done" },
  { id: "P2", name: "Backtester and cost model", state: "done" },
  { id: "P3", name: "Validation", state: "done" },
  { id: "P3.5", name: "One scorecard for every strategy", state: "done" },
  { id: "P4", name: "Paper trading (testnet); live later", state: "done" },
  { id: "Next", name: "650-day paper test: trend-vol-target BTC", state: "active" },
  { id: "P5", name: "Perpetuals (not offered to AU retail)", state: "parked" },
  { id: "P3+", name: "Multi-strategy portfolios", state: "parked" },
  { id: "P4+", name: "Settings: risk limits, exchange", state: "parked" },
];

const PHASE_STATUS: Record<PhaseState, { tone: "ok" | "info" | "neutral" | "warn"; label: string }> = {
  done: { tone: "ok", label: "Done" },
  active: { tone: "info", label: "In progress" },
  planned: { tone: "neutral", label: "Planned" },
  parked: { tone: "neutral", label: "Parked" },
};

export function Overview({ jobs, go, openJobs }: { jobs: Job[]; go: (r: Route) => void; openJobs: () => void }) {
  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="What to work on, and the evidence behind it."
        actions={
          <button className="btn btn-primary" onClick={() => go("data")}>
            Ingest data
          </button>
        }
      />

      <Section
        title="What to pursue"
        description={
          <>
            One decision per strategy, made from all the evidence below and recorded with its reason in
            research/decisions.toml. Pursue means carry on to paper trading; a pass needs the full{" "}
            <a href="#/rules">scorecard</a>.
          </>
        }
      >
        <DecisionsBoard />
      </Section>

      <Section
        title="Holdout tests"
        description="The one look at the year reserved before any research (from 2025-11-01), for a finished strategy, under rules frozen in its note. Run once from the CLI; shown here, never run here."
      >
        <HoldoutBoard />
      </Section>

      <Section
        title="Research pipeline"
        description="Every hypothesis and where it stands. Performance joins this table only once a strategy has out-of-sample results (P3), deflated for every trial logged against it."
      >
        <ResearchBoard />
      </Section>

      <Section
        title="Validation"
        description="Robustness checks and walk-forwards. Every setting they tried counts as a trial in the deflated Sharpe."
      >
        <StudiesBoard />
      </Section>

      <p className="small tertiary">
        Paper trading (P4) adds live equity against the backtest, risk limits and a kill switch to this page.
      </p>

      <div className="split">
        <Section title="Data">
          <DataSummary jobs={jobs} openJobs={openJobs} />
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

function DataSummary({ jobs, openJobs }: { jobs: Job[]; openJobs: () => void }) {
  const datasets = useResource(api.datasets, [], 10_000);
  const ds = datasets.data ?? [];
  const symbols = [...new Set(ds.map((d) => d.symbol))];
  const intervals = [...new Set(ds.map((d) => d.interval))];
  const totalBars = ds.reduce((s, d) => s + d.rows, 0);
  const earliest = ds.map((d) => d.first).filter(Boolean).sort()[0];
  const latest = ds.map((d) => d.last).filter(Boolean).sort().at(-1);
  const lastIngest = jobs.find((j) => j.kind === "ingest" && j.status === "succeeded");

  return (
    <Panel>
      <div className="data-line">
        <div>
          <span className="small secondary">Datasets</span>
          <b>{num(ds.length)}</b>
          <span className="small tertiary">
            {datasetsLine(symbols, intervals)}
          </span>
        </div>
        <div>
          <span className="small secondary">Bars stored</span>
          <b>{num(totalBars)}</b>
          <span className="small tertiary">Audited on the Data page</span>
        </div>
        <div>
          <span className="small secondary">Coverage</span>
          <b>{earliest ? date(earliest).slice(0, 7) : "None"}</b>
          <span className="small tertiary">{latest ? `to ${date(latest)}` : "No bars stored"}</span>
        </div>
        <div>
          <span className="small secondary">Last ingest</span>
          {lastIngest ? (
            <>
              <b>{ago(lastIngest.finished_at)}</b>
              <button className="link-btn small" onClick={openJobs}>
                View jobs
              </button>
            </>
          ) : (
            <>
              <b>None</b>
              <span className="small tertiary">Since the server started</span>
            </>
          )}
        </div>
      </div>
    </Panel>
  );
}

function ResearchBoard() {
  const research = useResource(api.researchList, []);
  const validation = useResource(api.validation, []);
  const v = validation.data;
  const list = research.data ?? [];
  const { live, closed } = splitClosed(list, (r) => isSettled(r.status));
  const [showRejected, setShowRejected] = useState(false);
  const open = (slug: string) => {
    window.location.hash = `/strategies/${slug}`;
  };
  return (
    <Panel>
      {list.length === 0 ? (
        <Empty title={research.loading ? "Loading research" : "No research yet"}>
          {!research.loading && <p>Write a hypothesis on the Strategies page, freeze it, then test it.</p>}
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Hypothesis</th>
                <th>Stage</th>
                <th>Note</th>
                <th>Implementation</th>
                <th className="num">
                  <abbr title="Distinct configurations tested. Every one counts against the result in P3's deflated Sharpe.">
                    Trials
                  </abbr>
                </th>
                <th className="num">
                  <abbr title="Deflated Sharpe: chance the best trial's true Sharpe beats the best of every strategy tried by luck">
                    Deflated
                  </abbr>
                </th>
              </tr>
            </thead>
            <tbody>
              {(showRejected ? [...live, ...closed] : live).map((r) => (
                <tr key={r.slug} className="clickable" onClick={() => open(r.slug)}>
                  <td style={{ fontWeight: 500 }}>{r.name}</td>
                  <td className="nw">
                    <Status tone={RESEARCH_STATUS[r.status].tone}>{RESEARCH_STATUS[r.status].label}</Status>
                  </td>
                  <td className="nw secondary">
                    v{r.version} {r.frozen ? "frozen" : "draft"}
                  </td>
                  <td className="mono small">{r.implementation ?? <span className="tertiary">not chosen</span>}</td>
                  <td className="num">{num(r.trials)}</td>
                  <td className="num">{r.deflated_sharpe == null ? <span className="tertiary">—</span> : `${Math.round(r.deflated_sharpe * 100)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <FoldToggle
            count={closed.length}
            noun={`rejected or validated idea${closed.length === 1 ? "" : "s"}`}
            open={showRejected}
            onToggle={() => setShowRejected((v) => !v)}
          />
        </div>
      )}
      <div className="board-foot">
        {v && v.trials > 1 && (
          <span>
            {v.trials} strategies tried so far: by luck alone the best of them would reach a Sharpe of about{" "}
            {v.expected_max_sharpe.toFixed(2)}, so a result has to beat that to stand out.
          </span>
        )}
        <span>In-sample results never appear on this page.</span>
        <span>The holdout is used once per finished strategy; see Holdout tests above.</span>
      </div>
    </Panel>
  );
}

/** Studies shown before "Show": the newest few. Every one still counts as trials. */
const STUDIES_SHOWN = 8;

function StudiesBoard() {
  const validation = useResource(api.validation, []);
  const all = validation.data?.studies ?? [];
  const [showOlder, setShowOlder] = useState(false);
  const { shown, older } = newestFirst(all, STUDIES_SHOWN);
  const studies = showOlder ? all : shown;
  return (
    <Panel>
      {studies.length === 0 ? (
        <Empty title={validation.loading ? "Loading studies" : "No studies yet"}>
          {!validation.loading && (
            <p>
              Run <span className="mono">kestrel robustness</span> or <span className="mono">kestrel walk-forward</span>{" "}
              on a strategy; each study appears here.
            </p>
          )}
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Study</th>
                <th>Market</th>
                <th>Period</th>
                <th className="num">Settings tried</th>
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {studies.map((s) => (
                <tr key={s.id}>
                  <td style={{ fontWeight: 500 }}>{studyTitle(s)}</td>
                  <td className="nw">{marketLabel(s)}</td>
                  <td className="nw secondary">
                    {s.start} to {s.end}
                  </td>
                  <td className="num">{num(s.looks.length)}</td>
                  <td className="nw">
                    <Status tone={studyTone(s.verdict)}>{s.verdict}</Status>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <FoldToggle
            count={older.length}
            noun={`older stud${older.length === 1 ? "y" : "ies"}`}
            open={showOlder}
            onToggle={() => setShowOlder((v) => !v)}
          />
        </div>
      )}
    </Panel>
  );
}

function DecisionsBoard() {
  const decisions = useResource(api.decisions, []);
  const list = decisions.data ?? [];
  const { live, closed } = splitClosed(list, (d) => d.decision === "drop");
  const [showDropped, setShowDropped] = useState(false);
  if (decisions.error) {
    return (
      <Panel>
        <Empty title="Couldn't read the decisions">
          <p>{decisions.error}</p>
        </Empty>
      </Panel>
    );
  }
  return (
    <Panel>
      {list.length === 0 ? (
        <Empty title={decisions.loading ? "Loading decisions" : "Nothing decided yet"}>
          {!decisions.loading && <p>Record a decision for each tested strategy in research/decisions.toml.</p>}
        </Empty>
      ) : (
        <ul className="decision-list">
          {(showDropped ? [...live, ...closed] : live).map((d) => (
            <li key={`${d.strategy} ${d.market}`} className={`decision-${d.decision}`}>
              <Status tone={DECISION[d.decision].tone}>{DECISION[d.decision].label}</Status>
              <div>
                <div className="decision-name">
                  {d.name} <span className="mono small tertiary">{d.strategy}</span>
                </div>
                <div className="small secondary">{d.market}</div>
                <p className="decision-reason">{d.reason}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
      <FoldToggle
        count={closed.length}
        noun={`dropped strateg${closed.length === 1 ? "y" : "ies"}`}
        open={showDropped}
        onToggle={() => setShowDropped((v) => !v)}
      />
    </Panel>
  );
}

function HoldoutBoard() {
  const tests = useResource(api.holdout, []);
  const list = tests.data ?? [];
  if (tests.error) {
    return (
      <Panel>
        <Empty title="Couldn't read the holdout records">
          <p>{tests.error}</p>
        </Empty>
      </Panel>
    );
  }
  return (
    <Panel>
      {list.length === 0 ? (
        <Empty title={tests.loading ? "Loading holdout tests" : "The holdout hasn't been used"}>
          {!tests.loading && <p>It is reserved for one final test of a finished strategy.</p>}
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table holdout-table">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Market</th>
                <th>Year scored</th>
                <th className="num">Return</th>
                <th>Edge</th>
                <th className="num">Worst drawdown</th>
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.research}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{r.strategy}</div>
                    <div className="mono small tertiary">{r.research}</div>
                  </td>
                  <td className="nw">{r.symbol}</td>
                  <td className="nw secondary">
                    {date(r.start)} to {date(new Date(new Date(r.end).getTime() - 86_400_000).toISOString())}
                  </td>
                  <td className="num nw">
                    {(r.mine.total_return * 100).toFixed(1)}%
                    <div className="small tertiary">holding {(r.held.total_return * 100).toFixed(1)}%</div>
                  </td>
                  <td>
                    <Status tone={r.edge_passes ? "ok" : "bad"}>{r.edge_passes ? "Pass" : "Fail"}</Status>
                    <div className="small secondary">{holdoutEdge(r)}</div>
                  </td>
                  <td className="num nw">
                    {(r.mine.max_drawdown * 100).toFixed(0)}%
                    <div className="small tertiary">holding {(r.held.max_drawdown * 100).toFixed(0)}%</div>
                  </td>
                  <td className="nw">
                    <Status tone={r.passes ? "ok" : "bad"}>{r.passes ? "Passed" : "Failed"}</Status>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
