import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, type Analysis, type Dataset, type Job, type Metrics, type RunDetail, type RunSummary } from "../api";
import { EquityChart } from "../components/EquityChart";
import { ParamInputs, defaultParams } from "../components/ParamInputs";
import { Empty, PageHeader, Panel, Section, Stat, Status } from "../components/ui";
import { ago, dateTime, lastResearchMonth, num } from "../format";
import { useResource } from "../hooks";
import { costHeadroom, money, span, trackRecord, tradeMarkers, usdt, verdict, MAX_LABELLED_TRADES } from "../summary";

const pct = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? "n/a" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(digits)}%`;
const plain = (x: number | null | undefined, digits = 2) =>
  x === null || x === undefined ? "n/a" : x.toFixed(digits);

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
            <th className="num">Net profit</th>
            <th className="num">vs holding BTC</th>
            <th className="num">Sharpe</th>
            <th className="num">Max drawdown</th>
            <th className="num">Trades</th>
            <th className="num">Run</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const m = r.metrics;
            const start = Number(r.initial_cash);
            const profit = m.final_equity - start;
            const edge = profit - (r.benchmark_metrics.final_equity - start);
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
                <td className={`num ${profit > 0 ? "up" : profit < 0 ? "down" : ""}`}>
                  {money(profit)}
                  <div className="cell-sub">{pct(m.total_return)}</div>
                </td>
                <td className={`num ${edge > 0 ? "up" : edge < 0 ? "down" : "secondary"}`}>
                  {Math.abs(edge) < 0.005 ? "same" : money(edge)}
                </td>
                <td className="num">{plain(m.sharpe)}</td>
                <td className="num">{pct(m.max_drawdown)}</td>
                <td className="num">{r.style === "brackets" ? (m.round_trips ?? 0) : m.trades}</td>
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
        <Verdict run={r} />
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
          <EquityChart equity={r.equity} benchmark={r.benchmark} trades={tradeMarkers(r)} />
          <p className="small tertiary chart-foot">
            Arrows mark trades on the equity line: up for buys, down for sells
            {r.style === "brackets"
              ? `; exits are green for a win and red for a loss${
                  r.trades.length <= MAX_LABELLED_TRADES ? ", labelled in R" : ". Every result is in the trade table below"
                }`
              : ""}
            .
          </p>
        </Panel>
        {r.analysis && <AnalysisPanels run={r} analysis={r.analysis} />}
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
        {r.style === "brackets" && <TradesPanel run={r} />}
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

const rFmt = (x: number | null | undefined) =>
  x === null || x === undefined ? "n/a" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}R`;

const prob = (x: number | null | undefined) => (x === null || x === undefined ? "n/a" : `${Math.round(x * 100)}%`);

function AnalysisPanels({ run: r, analysis: a }: { run: RunDetail; analysis: Analysis }) {
  const [lo, hi] = a.return_ci;
  const cur = r.account_currency;
  const isBenchmark = r.strategy === "buy-and-hold";
  const buckets = Object.entries(a.holding_buckets);
  const sold = buckets.reduce((s, [, n]) => s + n, 0);
  return (
    <>
      <div className="run-grid">
        <Panel pad>
          <h3 className="panel-title">Is the edge real?</h3>
          <Ledger
            rows={[
              ["Likely range of return", `${pct(lo, 0)} to ${pct(hi, 0)}`],
              ["Chance of a profit after costs", prob(a.p_profit)],
              ...(a.expectancy_ci
                ? ([
                    ["Likely range per trade", `${rFmt(a.expectancy_ci[0])} to ${rFmt(a.expectancy_ci[1])}`],
                    ["Chance each trade makes money on average", prob(a.p_positive_expectancy)],
                  ] as [string, string][])
                : []),
              ["Paper trading needed to trust it", trackRecord(a.min_track_record_days)],
            ]}
          />
          <p className="small tertiary panel-foot">
            From {a.resamples.toLocaleString("en-AU")} reshuffles of the history in {a.block_days}-day blocks. Assumes this was the only
            variant tried.
          </p>
        </Panel>
        <Panel pad>
          <h3 className="panel-title">Where the money comes from</h3>
          <Ledger
            rows={[
              ["Profit before costs", `${money(a.gross_profit)} ${cur}`],
              [
                "Break-even cost per round trip",
                a.break_even_cost_bps === null ? "n/a" : `${Math.round(a.break_even_cost_bps).toLocaleString("en-AU")} bps (paying ${a.cost_per_round_trip_bps?.toFixed(0)})`,
              ],
              ...(isBenchmark
                ? []
                : ([
                    ["Moves with BTC (beta)", plain(a.beta)],
                    ["Correlation with BTC", plain(a.correlation)],
                    ["BTC held at the same exposure", pct(a.matched_btc_return, 0)],
                  ] as [string, string][])),
            ]}
          />
          <p className="small tertiary panel-foot">{costHeadroom(a.break_even_cost_bps, a.cost_per_round_trip_bps)}</p>
        </Panel>
        <Panel pad>
          <h3 className="panel-title">What it's like to hold</h3>
          <Ledger
            rows={[
              ["Worst day", pct(a.worst_day)],
              ["Worst month", pct(a.worst_month)],
              ["Average of the worst 5% of days", pct(a.expected_shortfall, 2)],
              ...(a.payoff_ratio !== null
                ? ([["Average win ÷ average loss", `${a.payoff_ratio.toFixed(2)}`]] as [string, string][])
                : []),
              ...(a.profit_without_top5 !== null
                ? ([["Profit without the 5 best trades", `${money(a.profit_without_top5)} ${cur}`]] as [string, string][])
                : []),
            ]}
          />
        </Panel>
      </div>
      <div className="run-grid run-grid-2">
        <Panel pad>
          <h3 className="panel-title">Year by year</h3>
          <table className="mini-table">
            <thead>
              <tr>
                <th>Year</th>
                <th className="num">Strategy</th>
                <th className="num">Holding BTC</th>
              </tr>
            </thead>
            <tbody>
              {a.years.map((y) => (
                <tr key={y.year}>
                  <td>{y.year}</td>
                  <td className={`num mono ${(y.strategy ?? 0) >= 0 ? "up" : "down"}`}>{pct(y.strategy, 0)}</td>
                  <td className="num mono secondary">{pct(y.btc, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel pad>
          <h3 className="panel-title">Tax view (Australia, information only)</h3>
          {sold === 0 ? (
            <p className="small secondary">
              {a.open_at_end ? "Nothing was sold, so every gain is still unrealised." : "No positions were bought or sold."}
            </p>
          ) : (
            <Ledger
              rows={[
                ...(buckets.map(([k, n]) => [`Sold after ${k}`, `${n} position${n === 1 ? "" : "s"}`]) as [string, string][]),
                ["Gains held over 12 months", prob(a.long_term_gain_share)],
              ]}
            />
          )}
          <p className="small tertiary panel-foot">
            Gains on assets held over 12 months can get the CGT discount. Sells are matched to buys first-in first-out. Not
            tax advice.
            {a.open_at_end && sold > 0 ? " A position was still open at the end, so part of the gain is unrealised." : ""}
          </p>
        </Panel>
      </div>
    </>
  );
}

function TradesPanel({ run }: { run: RunDetail }) {
  const m = run.metrics;
  const risk = (run.config as { backtest?: { risk_per_trade?: number } }).backtest?.risk_per_trade;
  return (
    <Panel>
      <div className="code-head">
        <h3 className="panel-title">Trades in R</h3>
        <span className="small tertiary">
          1R = the loss if a stop is hit{risk ? `, sized at ${(risk * 100).toFixed(1)}% of equity` : ""}. After all
          costs.
        </span>
      </div>
      <div className="stats r-stats">
        <Stat label="Expectancy" value={rFmt(m.expectancy_r)} note="Average result per trade" />
        <Stat
          label="Win rate"
          value={m.win_rate === null || m.win_rate === undefined ? "n/a" : `${Math.round(m.win_rate * 100)}%`}
          note={`avg win ${rFmt(m.avg_win_r)} · avg loss ${rFmt(m.avg_loss_r)}`}
        />
        <Stat
          label="Profit factor"
          value={m.profit_factor === null || m.profit_factor === undefined ? "n/a" : m.profit_factor.toFixed(2)}
          note="Winning R ÷ losing R"
        />
        <Stat
          label="Costs per trade"
          value={rFmt(m.avg_cost_r)}
          note={m.risk_capped ? `${m.risk_capped} trades capped by cash` : "Share of each risk unit lost to costs"}
        />
      </div>
      {run.trades.length > 0 && (
        <div className="table-wrap">
          <table className="table nowrap">
            <thead>
              <tr>
                <th>Entry</th>
                <th>Exit</th>
                <th>Reason</th>
                <th className="num">Entry price</th>
                <th className="num">Stop</th>
                <th className="num">Target</th>
                <th className="num">Exit price</th>
                <th className="num">Net</th>
                <th className="num">Result</th>
              </tr>
            </thead>
            <tbody>
              {run.trades.map((t, i) => (
                <tr key={i}>
                  <td>{dateTime(t.entry_time)}</td>
                  <td>{dateTime(t.exit_time)}</td>
                  <td>
                    <Status tone={t.exit_reason === "target" ? "ok" : t.exit_reason === "stop" ? "bad" : "neutral"}>
                      {t.exit_reason}
                    </Status>
                    {t.risk_capped && <span className="chip run-chip">capped</span>}
                  </td>
                  <td className="num mono">{usdt(t.entry_price)}</td>
                  <td className="num mono">{usdt(t.stop)}</td>
                  <td className="num mono">{usdt(t.target)}</td>
                  <td className="num mono">{usdt(t.exit_price)}</td>
                  <td className={`num mono ${t.pnl >= 0 ? "up" : "down"}`}>{usdt(t.pnl)}</td>
                  <td className={`num mono ${t.r_multiple >= 0 ? "up" : "down"}`}>{rFmt(t.r_multiple)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/** The plain-English answer: did it make money, did it beat holding BTC, can we trust it? */
function Verdict({ run: r }: { run: RunDetail }) {
  const m = r.metrics;
  const start = Number(r.initial_cash);
  const cur = r.account_currency;
  const { tone, headline, trust, profit, btcProfit, vsBtc, isBenchmark, trades, wins } = verdict(r);

  return (
    <Panel>
      <div className={`verdict verdict-${tone}`}>
        <h3>{headline}</h3>
        <p className="secondary">{trust}</p>
      </div>
      <div className="stats stats-5">
        <Stat
          label="Net profit"
          value={<span className={profit > 0 ? "up" : profit < 0 ? "down" : undefined}>{money(profit)} {cur}</span>}
          note={`${pct(m.total_return)} on ${usdt(start)} ${cur}, after all costs`}
        />
        <Stat
          label="Compared with holding BTC"
          value={
            isBenchmark ? (
              "—"
            ) : (
              <span className={vsBtc > 0 ? "up" : vsBtc < 0 ? "down" : undefined}>
                {money(vsBtc)} {cur}
              </span>
            )
          }
          note={`Holding BTC: ${money(btcProfit)} ${cur}`}
        />
        <Stat label="Test period" value={span(m.start, m.end)} note={`${m.start.slice(0, 10)} to ${m.end.slice(0, 10)}`} />
        <Stat
          label="Trades taken"
          value={num(trades)}
          note={
            r.style === "brackets"
              ? `${wins} won · ${trades - wins} lost`
              : `${r.fills.filter((x) => x.side === "buy").length} buys · ${r.fills.filter((x) => x.side === "sell").length} sells${m.rejections ? ` · ${m.rejections} rejected` : ""}`
          }
        />
        <Stat
          label="Time in the market"
          value={`${Math.round(m.exposure * 100)}%`}
          note="The rest of the time it held cash"
        />
      </div>
    </Panel>
  );
}

/** Risk detail beneath the verdict: none of these repeat the verdict's numbers. */
function Headline({ run }: { run: RunDetail }) {
  const m = run.metrics;
  const b = run.benchmark_metrics;
  const [lo, hi] = m.sharpe_ci;
  return (
    <Panel>
      <div className="stats">
        <Stat
          label="Yearly return"
          value={pct(m.cagr)}
          note={`Holding BTC: ${pct(b.cagr)} a year`}
        />
        <Stat
          label="Sharpe"
          value={plain(m.sharpe)}
          note={m.sharpe === null ? "Undefined for flat equity" : `95% CI ${plain(lo)} to ${plain(hi)}`}
        />
        <Stat
          label="Worst drop"
          value={pct(m.max_drawdown)}
          note={`${Math.round(m.longest_underwater_days)} days at worst to recover`}
        />
        <Stat
          label="Costs paid"
          value={`${usdt(m.total_costs)} ${run.account_currency}`}
          note="Fees, spread and impact, already in the profit"
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
