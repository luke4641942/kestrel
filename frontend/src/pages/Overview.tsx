import { api, type Job } from "../api";
import type { Route } from "../App";
import { Empty, PageHeader, Panel, Section, Stat, Status } from "../components/ui";
import { ago, date, num } from "../format";
import { useResource } from "../hooks";

type PhaseState = "done" | "active" | "planned" | "parked" | "rethink";

// Parked items are agreed ideas waiting on an earlier phase; `id` says which.
const PHASES: { id: string; name: string; state: PhaseState }[] = [
  { id: "P1", name: "Data infrastructure", state: "done" },
  { id: "P1.5", name: "API and dashboard", state: "done" },
  { id: "P2", name: "Backtester and cost model", state: "active" },
  { id: "P3", name: "Validation", state: "planned" },
  { id: "P4", name: "Paper and live execution", state: "planned" },
  { id: "P5", name: "Perpetuals (not offered to AU retail)", state: "rethink" },
  { id: "P3+", name: "Multi-strategy portfolios", state: "parked" },
  { id: "P4+", name: "Settings: risk limits, exchange", state: "parked" },
];

const PHASE_STATUS: Record<PhaseState, { tone: "ok" | "info" | "neutral" | "warn"; label: string }> = {
  done: { tone: "ok", label: "Done" },
  active: { tone: "info", label: "In progress" },
  planned: { tone: "neutral", label: "Planned" },
  parked: { tone: "neutral", label: "Parked" },
  rethink: { tone: "warn", label: "Rethink" },
};

/**
 * Strategy board columns. The ranking is deliberately out-of-sample, after costs and
 * deflated for the number of variants tried: ranking by raw backtest return would
 * reward whichever of many variants got lucky.
 */
const STRATEGY_COLUMNS: { label: string; title: string; num?: boolean }[] = [
  { label: "Strategy", title: "Name, market and bar interval" },
  { label: "Stage", title: "Backtest, validated, paper or live" },
  { label: "OOS Sharpe", title: "Sharpe ratio on data the strategy was not tuned on, after all costs", num: true },
  {
    label: "Deflated",
    title: "Deflated Sharpe ratio: probability the true Sharpe is above zero given how many variants were tried",
    num: true,
  },
  { label: "Trials", title: "Variants logged in the trial log for this hypothesis", num: true },
  { label: "Cost drag", title: "Annual return lost to fees, spread and slippage", num: true },
  { label: "Drift", title: "Paper or live return minus the backtest on the same bars", num: true },
  { label: "90 days", title: "Recent equity curve" },
];

const Pending = () => <span className="tertiary">—</span>;

export function Overview({ jobs, go, openJobs }: { jobs: Job[]; go: (r: Route) => void; openJobs: () => void }) {
  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="Which strategies are earning their place, and whether paper trading matches the backtest."
        actions={
          <button className="btn btn-primary" onClick={() => go("data")}>
            Ingest data
          </button>
        }
      />

      <Section
        title="Portfolio"
        description="All figures after fees, spread and slippage."
        actions={<span className="small tertiary">From P4</span>}
      >
        <Panel>
          <div className="stats">
            <Stat label="Equity" value={<Pending />} note="Paper account opens in P4" />
            <Stat label="Return, 30 days" value={<Pending />} note="Shown against the backtest's expectation" />
            <Stat label="Drawdown from peak" value={<Pending />} note="Shown against its risk limit" />
            <Stat label="Costs, 30 days" value={<Pending />} note="Fees, spread and slippage paid" />
          </div>
        </Panel>
      </Section>

      <div className="split">
        <Section title="Paper equity against the backtest" actions={<span className="small tertiary">From P4</span>}>
          <Panel>
            <Empty title="No paper trading yet">
              <p>
                Paper equity will be drawn over the backtest run on the same bars, with its expected range. If the two
                drift apart, the backtest is missing something.
              </p>
            </Empty>
          </Panel>
        </Section>

        <Section title="Risk" actions={<span className="small tertiary">From P4</span>}>
          <Panel pad>
            <dl className="dl">
              <dt>Kill switch</dt>
              <dd>
                <Status>
                  <span className="secondary">Not built</span>
                </Status>
              </dd>
              <dt>Daily loss limit</dt>
              <dd className="secondary">Not set</dd>
              <dt>Largest position</dt>
              <dd className="secondary">No positions</dd>
              <dt>Exchange vs ledger</dt>
              <dd className="secondary">Not connected</dd>
            </dl>
          </Panel>
        </Section>
      </div>

      <Section
        title="Strategies"
        description="Ranked by out-of-sample Sharpe after costs, adjusted for how many variants were tried."
        actions={<span className="small tertiary">From P2</span>}
      >
        <Panel>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {STRATEGY_COLUMNS.map((c) => (
                    <th key={c.label} className={c.num ? "num" : undefined}>
                      <abbr title={c.title}>{c.label}</abbr>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td colSpan={STRATEGY_COLUMNS.length} style={{ padding: 0 }}>
                    <Empty title="No strategies yet">
                      <p>
                        Strategy research starts once the backtester is validated. Each strategy appears here after its
                        first out-of-sample run, with every variant tried counted against it.
                      </p>
                    </Empty>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="board-foot">
            <span>In-sample results never appear on this page.</span>
            <span>The holdout period stays untouched until a strategy is final.</span>
          </div>
        </Panel>
      </Section>

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
            {ds.length ? `${symbols.join(", ")} at ${intervals.join(", ")}` : "Ingest a dataset to begin"}
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
