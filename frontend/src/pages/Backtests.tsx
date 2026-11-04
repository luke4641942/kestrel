import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, type Dataset, type Job, type Metrics, type RunDetail, type RunSummary } from "../api";
import { EquityChart } from "../components/EquityChart";
import { ParamInputs, defaultParams } from "../components/ParamInputs";
import { Empty, PageHeader, Panel, Section, Stat, Status } from "../components/ui";
import { ago, dateTime, lastResearchMonth, num } from "../format";
import { useResource } from "../hooks";

const pct = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? "n/a" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(digits)}%`;
const plain = (x: number | null | undefined, digits = 2) =>
  x === null || x === undefined ? "n/a" : x.toFixed(digits);
const usdt = (x: number, digits = 2) =>
  x.toLocaleString("en-AU", { minimumFractionDigits: digits, maximumFractionDigits: digits });

const datasetKey = (d: Pick<Dataset, "symbol" | "interval">) => `${d.symbol}/${d.interval}`;

/** The run id in `#/backtests/<id>`, if any. */
function runFromHash(): string | undefined {
  const [, id] = window.location.hash.replace(/^#\/?/, "").split("/");
  return id || undefined;
}

export function Backtests({ jobs, onJobStarted }: { jobs: Job[]; onJobStarted: (job: Job) => void }) {
  const runs = useResource(api.backtests, []);
  const [selected, setSelected] = useState<string | undefined>(runFromHash);

  useEffect(() => {
    const onHash = () => setSelected(runFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // When a backtest job finishes, refresh the list and show the newest run. Skip the
  // first run of this effect so a link to a specific run (#/backtests/<id>) is kept.
  const finished = jobs.filter((j) => j.kind === "backtest" && j.finished_at).length;
  const mounted = useRef(false);
  useEffect(() => {
    runs.reload();
    if (mounted.current) setSelected(undefined);
    mounted.current = true;
  }, [finished, runs.reload]);
  const select = (id: string) => {
    window.location.hash = `/backtests/${id}`;
  };

  const list = runs.data ?? [];
  const current = list.find((r) => r.id === selected) ?? list[0];

  return (
    <>
      <PageHeader
        title="Backtests"
        subtitle="Strategies run through the event-driven engine, after costs, against BTC buy-and-hold on the same bars."
      />

      <Section title="New backtest">
        <Panel pad>
          <BacktestForm onStarted={onJobStarted} />
        </Panel>
      </Section>

      <Section title="Runs" description="Every saved run, newest first. Runs started from the CLI appear here too.">
        <Panel>
          {list.length === 0 ? (
            <Empty title={runs.loading ? "Loading runs" : "No runs yet"}>
              {!runs.loading && <p>Start a backtest above. Results are saved, so they stay here after a restart.</p>}
            </Empty>
          ) : (
            <RunsTable runs={list} current={current?.id} onSelect={select} />
          )}
        </Panel>
      </Section>

      {current && <RunView key={current.id} id={current.id} />}
    </>
  );
}

function BacktestForm({ onStarted }: { onStarted: (job: Job) => void }) {
  const datasets = useResource(api.datasets, []);
  const strategies = useResource(api.strategies, []);
  const config = useResource(api.costs, []);
  const research = useResource(api.researchList, []);
  const [dataset, setDataset] = useState<string>();
  // "impl:<key>" for benchmarks and engine tests, "research:<slug>" for a frozen note.
  const [choice, setChoice] = useState("impl:buy-and-hold");
  const [params, setParams] = useState<Record<string, number>>({});
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [cash, setCash] = useState("330");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const maxMonth = config.data ? lastResearchMonth(config.data.backtest.holdout_start) : undefined;
  const ds = datasets.data?.find((d) => datasetKey(d) === dataset) ?? datasets.data?.[0];
  const direct = (strategies.data ?? []).filter((s) => s.kind !== "research");
  const testable = (research.data ?? []).filter((r) => r.frozen && r.status !== "rejected" && r.implementation);
  const [kind, value] = choice.split(":") as ["impl" | "research", string];
  const chosenResearch = kind === "research" ? testable.find((r) => r.slug === value) : undefined;
  const implKey = kind === "research" ? chosenResearch?.implementation : value;
  const impl = strategies.data?.find((s) => s.key === implKey);
  useEffect(() => {
    setParams(impl ? defaultParams(impl.params) : {});
  }, [impl?.key]);

  // Default the range to the dataset's history, stopping before the holdout.
  useEffect(() => {
    if (!ds?.first || !ds.last || !maxMonth) return;
    setStart(ds.first.slice(0, 7));
    const last = ds.last.slice(0, 7);
    setEnd(last < maxMonth ? last : maxMonth);
  }, [ds?.symbol, ds?.interval, ds?.first, ds?.last, maxMonth]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ds) return;
    setBusy(true);
    setError(undefined);
    try {
      onStarted(
        await api.startBacktest({
          symbol: ds.symbol,
          interval: ds.interval,
          strategy: implKey ?? "buy-and-hold",
          start,
          end,
          initial_cash: Number(cash),
          params,
          research: chosenResearch?.slug,
        }),
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (datasets.data && datasets.data.length === 0) {
    return <p className="secondary">Ingest a dataset on the Data page first; backtests run on stored bars.</p>;
  }
  const description = chosenResearch
    ? `Runs ${chosenResearch.name} v${chosenResearch.version} with ${implKey} and logs it as a trial.`
    : impl?.description;

  return (
    <form onSubmit={submit}>
      <div className="form form-backtest">
        <div className="field">
          <label htmlFor="bt-data">Market</label>
          <select id="bt-data" value={ds ? datasetKey(ds) : ""} onChange={(e) => setDataset(e.target.value)}>
            {datasets.data?.map((d) => (
              <option key={datasetKey(d)} value={datasetKey(d)}>
                {d.symbol} {d.interval}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="bt-strategy">Strategy</label>
          <select id="bt-strategy" value={choice} onChange={(e) => setChoice(e.target.value)}>
            <optgroup label="Benchmark and engine tests">
              {direct.map((s) => (
                <option key={s.key} value={`impl:${s.key}`}>
                  {s.key}
                </option>
              ))}
            </optgroup>
            {testable.length > 0 && (
              <optgroup label="Research (frozen notes, logged as trials)">
                {testable.map((r) => (
                  <option key={r.slug} value={`research:${r.slug}`}>
                    {r.name} v{r.version}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </div>
        <ParamInputs params={impl?.params ?? []} values={params} onChange={setParams} idPrefix="bt" />
        <div className="field">
          <label htmlFor="bt-start">From month</label>
          <input id="bt-start" type="month" value={start} max={maxMonth} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="bt-end">To month</label>
          <input id="bt-end" type="month" value={end} max={maxMonth} onChange={(e) => setEnd(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="bt-cash">Starting cash (USDT)</label>
          <input id="bt-cash" type="number" min={1} step="any" value={cash} onChange={(e) => setCash(e.target.value)} />
        </div>
        <button className="btn btn-primary" disabled={busy || !ds || !start || !end || !(Number(cash) > 0)}>
          {busy ? "Starting" : "Run backtest"}
        </button>
      </div>
      {error ? (
        <div className="form-error">{error}</div>
      ) : (
        <p className="form-hint">
          {description ? `${description} ` : ""}330 USDT is about A$500. Data from{" "}
          {maxMonth ? `after ${maxMonth}` : "the holdout period"} is reserved for one final test and can't be used
          here.
        </p>
      )}
    </form>
  );
}

function RunsTable({
  runs,
  current,
  onSelect,
}: {
  runs: RunSummary[];
  current?: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="table-wrap">
      <table className="table nowrap">
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Market</th>
            <th>Period</th>
            <th className="num">Return</th>
            <th className="num">vs BTC</th>
            <th className="num">Sharpe</th>
            <th className="num">Max drawdown</th>
            <th className="num">Trades</th>
            <th className="num">Run</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const m = r.metrics;
            const edge = m.total_return - r.benchmark_metrics.total_return;
            return (
              <tr
                key={r.id}
                className={`clickable${r.id === current ? " selected" : ""}`}
                onClick={() => onSelect(r.id)}
              >
                <td>
                  <div style={{ fontWeight: 500 }}>
                    {r.strategy}
                    {r.used_holdout && <span className="chip verify run-chip">holdout</span>}
                  </div>
                  <div className="cell-sub">
                    {r.research ? `trial of ${r.research.slug} v${r.research.version}` : r.strategy_kind}
                    {Object.keys(r.params).length > 0 &&
                      ` · ${Object.entries(r.params)
                        .map(([k, v]) => `${k}=${v}`)
                        .join(" ")}`}
                  </div>
                </td>
                <td>
                  {r.symbol} {r.interval}
                </td>
                <td className="secondary">
                  {r.start} to {r.end}
                </td>
                <td className="num">{pct(m.total_return)}</td>
                <td className={`num ${edge > 0 ? "up" : edge < 0 ? "down" : "secondary"}`}>
                  {Math.abs(edge) < 5e-5 ? "same" : pct(edge)}
                </td>
                <td className="num">{plain(m.sharpe)}</td>
                <td className="num">{pct(m.max_drawdown)}</td>
                <td className="num">{m.trades}</td>
                <td className="num secondary">{ago(r.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RunView({ id }: { id: string }) {
  const run = useResource(() => api.backtest(id), [id]);
  if (run.error) {
    return (
      <Section title="Run">
        <Panel>
          <Empty title="Couldn't load this run">
            <p>{run.error}</p>
          </Empty>
        </Panel>
      </Section>
    );
  }
  const r = run.data;
  if (!r) return null;
  return (
    <Section
      title={`${r.strategy_name}, ${r.start} to ${r.end}`}
      description={`${r.symbol} ${r.interval} · started with ${usdt(Number(r.initial_cash))} ${r.account_currency} · decisions at each bar close, filled at the next open`}
    >
      <div className="run-stack">
        <Headline run={r} />
        <Panel pad>
          <div className="chart-top">
            <span className="small secondary">Equity in {r.account_currency}, drawdown underneath</span>
            <span className="cost-key">
              <span>
                <i className="swatch" style={{ background: "var(--text)" }} />
                {r.strategy}
              </span>
              <span>
                <i className="swatch swatch-dashed" />
                BTC buy-and-hold
              </span>
            </span>
          </div>
          <EquityChart equity={r.equity} benchmark={r.benchmark} />
        </Panel>
        <div className="run-grid">
          <Panel pad>
            <h3 className="panel-title">Costs and activity</h3>
            <Ledger
              rows={[
                ["Fees", `${usdt(r.metrics.fees, 4)} ${r.account_currency}`],
                ["Spread", `${usdt(r.metrics.spread_costs, 4)} ${r.account_currency}`],
                ["Market impact", `${usdt(r.metrics.impact_costs, 4)} ${r.account_currency}`],
                ["Total costs", `${usdt(r.metrics.total_costs, 4)} ${r.account_currency}`],
                ["Turnover", `${plain(r.metrics.turnover)}× equity per year`],
                ["Exposure", `${(r.metrics.exposure * 100).toFixed(0)}% invested on average`],
                ["No-trade band skips", num(r.metrics.band_skips)],
                ["Rejected orders", num(r.metrics.rejections)],
              ]}
            />
          </Panel>
          <Panel pad>
            <h3 className="panel-title">Risk and return detail</h3>
            <MetricsLedger m={r.metrics} b={r.benchmark_metrics} />
          </Panel>
          <Panel pad>
            <h3 className="panel-title">Provenance</h3>
            <Ledger
              rows={[
                ["Run", <span className="mono">{r.id}</span>],
                ["Created", dateTime(r.created_at)],
                ["Config", <span className="mono">{r.config_fingerprint}</span>],
                [
                  "Code",
                  r.code?.commit ? (
                    <span className="mono">
                      {r.code.commit.slice(0, 10)}
                      {r.code.dirty && <span className="warn"> + uncommitted</span>}
                    </span>
                  ) : (
                    "unknown"
                  ),
                ],
                [
                  "Holdout",
                  r.used_holdout ? (
                    <Status tone="warn">Used</Status>
                  ) : (
                    <span className="secondary">Not touched</span>
                  ),
                ],
              ]}
            />
          </Panel>
        </div>
        <Panel>
          <FillsTable run={r} />
        </Panel>
        <p className="small tertiary">
          Confidence intervals assume this was the only strategy tried. P3's deflated Sharpe will correct for every
          variant tested. Max drawdown is one history's worst case: treat it as a floor, not a forecast.
        </p>
      </div>
    </Section>
  );
}

function Headline({ run }: { run: RunDetail }) {
  const m = run.metrics;
  const b = run.benchmark_metrics;
  const [lo, hi] = m.sharpe_ci;
  return (
    <Panel>
      <div className="stats">
        <Stat
          label="Return"
          value={pct(m.total_return)}
          note={`BTC buy-and-hold ${pct(b.total_return)} · CAGR ${pct(m.cagr)}`}
        />
        <Stat
          label="Sharpe"
          value={plain(m.sharpe)}
          note={
            m.sharpe === null
              ? "Undefined for flat equity"
              : `95% CI ${plain(lo)} to ${plain(hi)} · P(true > 0) ${m.psr === null ? "n/a" : `${Math.round(m.psr * 100)}%`}`
          }
        />
        <Stat
          label="Max drawdown"
          value={pct(m.max_drawdown)}
          note={`${Math.round(m.longest_underwater_days)} days underwater at worst`}
        />
        <Stat
          label="Trades"
          value={num(m.trades)}
          note={
            m.few_trades ? (
              <span className="warn">Too few to trust per-trade statistics</span>
            ) : (
              `${num(m.rejections)} rejected`
            )
          }
        />
      </div>
    </Panel>
  );
}

function MetricsLedger({ m, b }: { m: Metrics; b: Metrics }) {
  const rows: [string, string, string][] = [
    ["CAGR", pct(m.cagr), pct(b.cagr)],
    ["Volatility", m.volatility === null ? "n/a" : `${(m.volatility * 100).toFixed(1)}%`, b.volatility === null ? "n/a" : `${(b.volatility * 100).toFixed(1)}%`],
    ["Sortino", plain(m.sortino), plain(b.sortino)],
    ["Skew", plain(m.skew), plain(b.skew)],
    ["Kurtosis", plain(m.kurtosis), plain(b.kurtosis)],
    ["Final equity", usdt(m.final_equity), usdt(b.final_equity)],
  ];
  return (
    <table className="mini-table">
      <thead>
        <tr>
          <th />
          <th className="num">Strategy</th>
          <th className="num">BTC</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, s, bench]) => (
          <tr key={label}>
            <td>{label}</td>
            <td className="num mono">{s}</td>
            <td className="num mono secondary">{bench}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Ledger({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="dl">
      {rows.map(([label, value]) => (
        <LedgerRow key={label} label={label} value={value} />
      ))}
    </dl>
  );
}

function LedgerRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function FillsTable({ run }: { run: RunDetail }) {
  if (run.fills.length === 0 && run.rejections.length === 0) {
    return (
      <Empty title="No trades">
        <p>The strategy never asked for a position.</p>
      </Empty>
    );
  }
  return (
    <div className="table-wrap">
      <table className="table nowrap">
        <thead>
          <tr>
            <th>Filled at (bar open)</th>
            <th>Side</th>
            <th className="num">Quantity</th>
            <th className="num">Reference</th>
            <th className="num">Fill price</th>
            <th className="num">Fee</th>
            <th className="num">Spread</th>
            <th className="num">Impact</th>
            <th className="num">Cash flow</th>
          </tr>
        </thead>
        <tbody>
          {run.fills.map((f, i) => (
            <tr key={`f${i}`}>
              <td>{dateTime(f.time)}</td>
              <td>
                <Status tone={f.side === "buy" ? "ok" : "bad"}>{f.side === "buy" ? "Buy" : "Sell"}</Status>
              </td>
              <td className="num mono">{f.quantity.toFixed(5)}</td>
              <td className="num mono">{usdt(f.reference_price)}</td>
              <td className="num mono">{usdt(f.price)}</td>
              <td className="num mono">{usdt(f.fee, 4)}</td>
              <td className="num mono">{usdt(f.spread_cost, 4)}</td>
              <td className="num mono">{usdt(f.impact_cost, 4)}</td>
              <td className={`num mono ${f.cash_flow < 0 ? "down" : "up"}`}>{usdt(f.cash_flow)}</td>
            </tr>
          ))}
          {run.rejections.map((x, i) => (
            <tr key={`r${i}`}>
              <td>{dateTime(x.time)}</td>
              <td>
                <Status tone="warn">Rejected</Status>
              </td>
              <td className="num mono">{x.quantity.toFixed(5)}</td>
              <td colSpan={6} className="secondary">
                {x.reason}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
