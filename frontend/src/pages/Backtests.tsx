import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, type Dataset, type Job, type Metrics, type RunDetail, type RunSummary } from "../api";
import { EquityChart } from "../components/EquityChart";
import { ParamInputs, defaultParams } from "../components/ParamInputs";
import { Empty, PageHeader, Panel, Section, Stat, Status, Tabs } from "../components/ui";
import { ago, dateTime, lastResearchMonth, num } from "../format";
import { useResource } from "../hooks";
import {
  MAX_LABELLED_TRADES,
  DEFAULT_RUN_SORT,
  costHeadroom,
  defaultDataset,
  fewTrades,
  heldAsset,
  marketLabel,
  marketsFor,
  money,
  nextSort,
  paramsText,
  runRole,
  sortRuns,
  type RunSort,
  type RunSortKey,
  span,
  tooManyToMark,
  trackRecord,
  tradeCount,
  tradeMarkers,
  usdt,
  verdict,
} from "../summary";

const pct = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? "n/a" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(digits)}%`;
const plain = (x: number | null | undefined, digits = 2) =>
  x === null || x === undefined ? "n/a" : x.toFixed(digits);
const rFmt = (x: number | null | undefined) =>
  x === null || x === undefined ? "n/a" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}R`;
const prob = (x: number | null | undefined) => (x === null || x === undefined ? "n/a" : `${Math.round(x * 100)}%`);
const tone = (x: number) => (x > 0 ? "up" : x < 0 ? "down" : undefined);

const datasetKey = (d: Pick<Dataset, "symbol" | "interval">) => `${d.symbol}/${d.interval}`;

/** Long lists start short; the rest is one click away. */
const RUNS_SHOWN = 10;
const ROWS_SHOWN = 50;

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
        subtitle="Strategies run through the event-driven engine, after costs, against buy-and-hold of the same market on the same bars."
      />

      <Section title="New backtest">
        <Panel pad>
          <BacktestForm onStarted={onJobStarted} />
        </Panel>
      </Section>

      <Section title="Runs" description="Every saved run, including runs started from the CLI. Click a heading to sort; select a run to see it below.">
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
  const direct = (strategies.data ?? []).filter((s) => s.kind !== "research");
  const testable = (research.data ?? []).filter((r) => r.frozen && r.status !== "rejected" && r.implementation);
  const [kind, value] = choice.split(":") as ["impl" | "research", string];
  const chosenResearch = kind === "research" ? testable.find((r) => r.slug === value) : undefined;
  const implKey = kind === "research" ? chosenResearch?.implementation : value;
  const impl = strategies.data?.find((s) => s.key === implKey);
  // A cross-sectional strategy runs on the whole coin universe, not a chosen market.
  const markets = marketsFor(impl, datasets.data ?? []);
  const ds = markets.find((d) => datasetKey(d) === dataset) ?? defaultDataset(markets);
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
  const description =
    impl?.universe && markets.length === 0
      ? "This strategy ranks every coin: it needs BTCUSDT 1d and the coin universe (kestrel ingest-universe --interval 1d)."
      : chosenResearch
        ? `Runs ${chosenResearch.name} v${chosenResearch.version} with ${implKey} and logs it as a trial.`
        : impl?.description;

  return (
    <form onSubmit={submit}>
      <div className="form form-backtest">
        <div className="field">
          <label htmlFor="bt-data">Market</label>
          <select id="bt-data" value={ds ? datasetKey(ds) : ""} onChange={(e) => setDataset(e.target.value)}>
            {markets.map((d) => (
              <option key={datasetKey(d)} value={datasetKey(d)}>
                {marketLabel(d)}
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
  const [all, setAll] = useState(false);
  const [sort, setSort] = useState<RunSort>(DEFAULT_RUN_SORT);
  const sorted = sortRuns(runs, sort);
  // A run linked directly (#/backtests/<id>) stays visible even if it is further down.
  const shown = all || sorted.findIndex((r) => r.id === current) >= RUNS_SHOWN ? sorted : sorted.slice(0, RUNS_SHOWN);
  const header = (key: RunSortKey, label: string, title: string) => (
    <SortHeader sortKey={key} label={label} title={title} sort={sort} onSort={(k) => setSort(nextSort(sort, k))} />
  );
  return (
    <>
      <div className="table-wrap">
        <table className="table runs-table">
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Market</th>
              <th>Period</th>
              {header("profit", "Net profit", "After all costs. Sorts by return, so different starting cash compares fairly")}
              {header("vsHold", "vs buy-and-hold", "Net profit minus what buy-and-hold of the same market made")}
              {header("sharpe", "Sharpe", "Annualised, from daily returns")}
              {header("drawdown", "Max drawdown", "Worst fall from a peak; sorts shallowest first")}
              {header("trades", "Trades", "Round trips for stop-and-target strategies, orders otherwise")}
              {header("saved", "Saved", "When the run was saved")}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const m = r.metrics;
              const start = Number(r.initial_cash);
              const profit = m.final_equity - start;
              const edge = profit - (r.benchmark_metrics.final_equity - start);
              const sub = `${runRole(r)} · ${paramsText(r.params)}`;
              return (
                <tr
                  key={r.id}
                  className={`clickable${r.id === current ? " selected" : ""}`}
                  onClick={() => onSelect(r.id)}
                >
                  <td>
                    <div className="run-name">
                      {r.strategy}
                      {r.used_holdout && <span className="chip verify run-chip">holdout</span>}
                    </div>
                    <div className="cell-sub truncate" title={sub}>
                      {sub}
                    </div>
                  </td>
                  <td className="nw">{marketLabel(r)}</td>
                  <td className="nw secondary">
                    {r.start} to {r.end}
                  </td>
                  <td className={`num ${tone(profit) ?? ""}`}>
                    {money(profit)}
                    <div className="cell-sub">{pct(m.total_return)}</div>
                  </td>
                  <td className={`num ${tone(edge) ?? "secondary"}`}>
                    {r.strategy === "buy-and-hold" ? "benchmark" : money(edge)}
                  </td>
                  <td className="num">{plain(m.sharpe)}</td>
                  <td className="num">{pct(m.max_drawdown)}</td>
                  <td className={`num${fewTrades(r) ? " warn" : ""}`} title={fewTrades(r) ? "Too few to trust" : undefined}>
                    {num(tradeCount(r))}
                  </td>
                  <td className="num secondary">{ago(r.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <MoreRows shown={shown.length} total={runs.length} noun="runs" onShowAll={() => setAll(true)} />
    </>
  );
}

/** A column heading that sorts the table. aria-sort tells screen readers the order. */
function SortHeader({
  sortKey,
  label,
  title,
  sort,
  onSort,
}: {
  sortKey: RunSortKey;
  label: string;
  title: string;
  sort: RunSort;
  onSort: (key: RunSortKey) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <th className="num" aria-sort={active ? (sort.dir === "desc" ? "descending" : "ascending") : "none"}>
      <button type="button" className={`sort-btn${active ? " active" : ""}`} title={title} onClick={() => onSort(sortKey)}>
        {label}
        <span className="sort-arrow" aria-hidden="true">
          {active && sort.dir === "asc" ? "↑" : "↓"}
        </span>
      </button>
    </th>
  );
}

function MoreRows({
  shown,
  total,
  noun,
  onShowAll,
}: {
  shown: number;
  total: number;
  noun: string;
  onShowAll: () => void;
}) {
  if (shown >= total) return null;
  return (
    <div className="table-more">
      <span className="small secondary">
        Showing {num(shown)} of {num(total)} {noun}.
      </span>
      <button type="button" className="link-btn" onClick={onShowAll}>
        Show all
      </button>
    </div>
  );
}

type RunTab = "stats" | "trades" | "costs" | "tax" | "details";

function RunView({ id }: { id: string }) {
  const run = useResource(() => api.backtest(id), [id]);
  const [tab, setTab] = useState<RunTab>("stats");
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
  const tabs: { id: RunTab; label: string }[] = [
    { id: "stats", label: "Statistics" },
    ...(r.style === "brackets" ? [{ id: "trades" as const, label: `Trades (${num(r.trades.length)})` }] : []),
    { id: "costs", label: `Costs and fills (${num(r.fills.length)})` },
    { id: "tax", label: "Tax view" },
    { id: "details", label: "Run details" },
  ];
  return (
    <Section
      title={r.strategy_name}
      description={`${marketLabel(r)} · ${r.start} to ${r.end} (${span(r.metrics.start, r.metrics.end)}) · ${usdt(
        Number(r.initial_cash),
      )} ${r.account_currency} starting cash · decided at each bar close, filled at the next open`}
    >
      <div className="run-stack">
        <RunSummaryPanel run={r} />
        <ChartPanel run={r} />
        <div>
          <Tabs label="Run detail" tabs={tabs} value={tab} onChange={setTab} />
          <div className="tab-body">
            {tab === "stats" && <StatisticsTab run={r} />}
            {tab === "trades" && <TradesTab run={r} />}
            {tab === "costs" && <CostsTab run={r} />}
            {tab === "tax" && <TaxTab run={r} />}
            {tab === "details" && <DetailsTab run={r} />}
          </div>
        </div>
        <p className="small tertiary">
          Confidence intervals and probabilities assume this was the only variant tried; P3's deflated Sharpe will correct
          for every variant tested. Max drawdown is one history's worst case: treat it as a floor, not a forecast.
        </p>
      </div>
    </Section>
  );
}

/**
 * The verdict, then the headline metrics in the agreed order: return against holding the
 * same market, Sharpe with its interval, drawdown, expectancy, cost against break-even,
 * and the number of trades (flagged when too few to trust).
 */
function RunSummaryPanel({ run: r }: { run: RunDetail }) {
  const m = r.metrics;
  const b = r.benchmark_metrics;
  const a = r.analysis;
  const cur = r.account_currency;
  const asset = heldAsset(r);
  const v = verdict(r);
  const [lo, hi] = m.sharpe_ci;
  const brackets = r.style === "brackets";
  const buys = r.fills.filter((x) => x.side === "buy").length;
  return (
    <Panel>
      <div className={`verdict verdict-${v.tone}`}>
        <h3>{v.headline}</h3>
        <p className="secondary">{v.trust}</p>
      </div>
      <div className="stats stats-8">
        <Stat
          label="Net profit"
          value={
            <span className={tone(v.profit)}>
              {money(v.profit)} {cur}
            </span>
          }
          note={`${pct(m.total_return)} on ${usdt(Number(r.initial_cash))} ${cur}, after all costs`}
        />
        <Stat
          label={`Against holding ${asset}`}
          value={
            v.isBenchmark ? (
              "—"
            ) : (
              <span className={tone(v.vsBtc)}>
                {money(v.vsBtc)} {cur}
              </span>
            )
          }
          note={`Holding ${asset}: ${money(v.btcProfit)} ${cur} (${pct(b.total_return, 0)})`}
        />
        <Stat
          label="Sharpe"
          value={plain(m.sharpe)}
          note={m.sharpe === null ? "Undefined for flat equity" : `95% CI ${plain(lo)} to ${plain(hi)} · P(SR > 0) ${prob(m.psr)}`}
        />
        <Stat
          label="Max drawdown"
          value={pct(m.max_drawdown)}
          note={`Longest under water: ${num(Math.round(m.longest_underwater_days))} days`}
        />
        {brackets ? (
          <Stat
            label="Expectancy per trade"
            value={<span className={tone(m.expectancy_r ?? 0)}>{rFmt(m.expectancy_r)}</span>}
            note={
              a?.expectancy_ci
                ? `95% CI ${rFmt(a.expectancy_ci[0])} to ${rFmt(a.expectancy_ci[1])}, after costs`
                : "After all costs"
            }
          />
        ) : (
          <Stat label="Yearly return" value={pct(m.cagr)} note={`Holding ${asset}: ${pct(b.cagr)} a year`} />
        )}
        <Stat
          label="Cost per round trip"
          value={a?.cost_per_round_trip_bps == null ? "n/a" : `${a.cost_per_round_trip_bps.toFixed(0)} bps`}
          note={
            a?.break_even_cost_bps == null
              ? "No trades"
              : `Break-even at ${Math.round(a.break_even_cost_bps).toLocaleString("en-AU")} bps`
          }
        />
        <Stat
          label="Trades"
          value={num(v.trades)}
          note={
            fewTrades(r) ? (
              <span className="warn">Too few to trust; aim for 30 or more</span>
            ) : brackets ? (
              `${v.wins} won · ${v.trades - v.wins} lost`
            ) : (
              `${buys} buys · ${r.fills.length - buys} sells`
            )
          }
        />
        <Stat label="Time in the market" value={`${Math.round(m.exposure * 100)}%`} note="Cash the rest of the time" />
      </div>
    </Panel>
  );
}

function ChartPanel({ run: r }: { run: RunDetail }) {
  const hidden = tooManyToMark(r);
  return (
    <Panel pad>
      <div className="chart-top">
        <span className="small secondary">Equity in {r.account_currency} (log scale), drawdown underneath</span>
        <span className="cost-key">
          <span>
            <i className="swatch" style={{ background: "var(--text)" }} />
            {r.strategy}
          </span>
          <span>
            <i className="swatch swatch-dashed" />
            {heldAsset(r)} buy-and-hold
          </span>
        </span>
      </div>
      <EquityChart equity={r.equity} benchmark={r.benchmark} trades={tradeMarkers(r)} />
      <p className="small tertiary chart-foot">
        {hidden
          ? `Too many trades to mark without hiding the line; ${
              r.style === "brackets" ? "the Trades tab lists every one" : "every fill is under Costs and fills"
            }.`
          : `Arrows mark trades: up for buys, down for sells${
              r.style === "brackets"
                ? `; exits are green for a win and red for a loss${r.trades.length <= MAX_LABELLED_TRADES ? ", labelled in R" : ""}`
                : ""
            }.`}{" "}
        A log scale shows equal percentage moves as equal heights, so a small account and the benchmark can share one
        axis.
      </p>
    </Panel>
  );
}

function StatisticsTab({ run: r }: { run: RunDetail }) {
  const a = r.analysis;
  const asset = heldAsset(r);
  const cur = r.account_currency;
  return (
    <div className="run-stack">
      {a && (
        <div className="run-grid">
          <Panel pad>
            <h3 className="panel-title">Is the edge real?</h3>
            <Ledger
              rows={[
                ["Return, 95% range", `${pct(a.return_ci[0], 0)} to ${pct(a.return_ci[1], 0)}`],
                ["Chance of a profit after costs", prob(a.p_profit)],
                ...(a.expectancy_ci
                  ? ([
                      ["Expectancy, 95% range", `${rFmt(a.expectancy_ci[0])} to ${rFmt(a.expectancy_ci[1])}`],
                      ["Chance the average trade is profitable", prob(a.p_positive_expectancy)],
                    ] as [string, string][])
                  : []),
                ["Paper trading needed to trust it", trackRecord(a.min_track_record_days)],
                ...(r.deflated_sharpe != null && r.deflated_trials
                  ? ([
                      [`Chance it's real after ${r.deflated_trials} strategies tried`, prob(r.deflated_sharpe)],
                    ] as [string, string][])
                  : []),
              ]}
            />
            <p className="small tertiary panel-foot">
              Returns: {a.resamples.toLocaleString("en-AU")} block-bootstrap resamples of daily returns in {a.block_days}
              -day blocks, which keeps streaks and volatility clustering. Trades: resampled individually.
            </p>
          </Panel>
          <Panel pad>
            <h3 className="panel-title">Where the money comes from</h3>
            <Ledger
              rows={[
                ["Profit before costs", `${money(a.gross_profit)} ${cur}`],
                [
                  "Break-even cost per round trip",
                  a.break_even_cost_bps === null
                    ? "n/a"
                    : `${Math.round(a.break_even_cost_bps).toLocaleString("en-AU")} bps (paying ${a.cost_per_round_trip_bps?.toFixed(0)})`,
                ],
                ...(r.strategy === "buy-and-hold"
                  ? []
                  : ([
                      [`Beta to ${asset}`, plain(a.beta)],
                      [`Correlation with ${asset}`, plain(a.correlation)],
                      [`${asset} held at the same exposure`, pct(a.matched_btc_return, 0)],
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
                ["Expected shortfall (worst 5% of days)", pct(a.expected_shortfall, 2)],
                ...(a.payoff_ratio !== null
                  ? ([["Average win ÷ average loss", a.payoff_ratio.toFixed(2)]] as [string, string][])
                  : []),
                ...(a.profit_without_top5 !== null
                  ? ([["Profit without the 5 best trades", `${money(a.profit_without_top5)} ${cur}`]] as [string, string][])
                  : []),
              ]}
            />
          </Panel>
        </div>
      )}
      <div className="run-grid run-grid-2">
        <Panel pad>
          <h3 className="panel-title">Against buy-and-hold</h3>
          <MetricsLedger m={r.metrics} b={r.benchmark_metrics} />
        </Panel>
        {a && (
          <Panel pad>
            <h3 className="panel-title">Year by year</h3>
            <table className="mini-table">
              <thead>
                <tr>
                  <th>Year</th>
                  <th className="num">Strategy</th>
                  <th className="num">Holding {asset}</th>
                </tr>
              </thead>
              <tbody>
                {a.years.map((y) => (
                  <tr key={y.year}>
                    <td>{y.year}</td>
                    <td className={`num mono ${tone(y.strategy ?? 0) ?? ""}`}>{pct(y.strategy, 0)}</td>
                    <td className="num mono secondary">{pct(y.btc, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}
      </div>
    </div>
  );
}

function MetricsLedger({ m, b }: { m: Metrics; b: Metrics }) {
  const vol = (x: number | null) => (x === null ? "n/a" : `${(x * 100).toFixed(1)}%`);
  const rows: [string, string, string][] = [
    ["Total return", pct(m.total_return), pct(b.total_return)],
    ["CAGR", pct(m.cagr), pct(b.cagr)],
    ["Volatility (annual)", vol(m.volatility), vol(b.volatility)],
    ["Sharpe", plain(m.sharpe), plain(b.sharpe)],
    ["Sortino", plain(m.sortino), plain(b.sortino)],
    ["Max drawdown", pct(m.max_drawdown), pct(b.max_drawdown)],
    ["Skew (daily)", plain(m.skew), plain(b.skew)],
    ["Kurtosis (daily)", plain(m.kurtosis), plain(b.kurtosis)],
    ["Final equity", usdt(m.final_equity), usdt(b.final_equity)],
  ];
  return (
    <table className="mini-table">
      <thead>
        <tr>
          <th />
          <th className="num">Strategy</th>
          <th className="num">Buy-and-hold</th>
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

function TradesTab({ run }: { run: RunDetail }) {
  const m = run.metrics;
  const [all, setAll] = useState(false);
  const risk = (run.config as { backtest?: { risk_per_trade?: number } }).backtest?.risk_per_trade;
  const shown = all ? run.trades : run.trades.slice(0, ROWS_SHOWN);
  return (
    <Panel>
      <div className="code-head">
        <h3 className="panel-title">Trades in R</h3>
        <span className="small tertiary">
          1R = the planned loss at the stop{risk ? `, sized at ${(risk * 100).toFixed(1)}% of equity` : ""}. Results are
          after all costs.
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
        <>
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
                  <th className="num">Net ({run.account_currency})</th>
                  <th className="num">Result</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((t, i) => (
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
                    <td className="num mono">{t.target === null ? "—" : usdt(t.target)}</td>
                    <td className="num mono">{usdt(t.exit_price)}</td>
                    <td className={`num mono ${t.pnl >= 0 ? "up" : "down"}`}>{usdt(t.pnl)}</td>
                    <td className={`num mono ${t.r_multiple >= 0 ? "up" : "down"}`}>{rFmt(t.r_multiple)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <MoreRows shown={shown.length} total={run.trades.length} noun="trades" onShowAll={() => setAll(true)} />
        </>
      )}
    </Panel>
  );
}

function CostsTab({ run: r }: { run: RunDetail }) {
  const m = r.metrics;
  const cur = r.account_currency;
  return (
    <div className="run-stack">
      <Panel pad>
        <h3 className="panel-title">Costs and activity</h3>
        <div className="ledger-cols">
          <Ledger
            rows={[
              ["Fees", `${usdt(m.fees, 2)} ${cur}`],
              ["Spread", `${usdt(m.spread_costs, 2)} ${cur}`],
              ["Market impact", `${usdt(m.impact_costs, 2)} ${cur}`],
              ["Total costs", `${usdt(m.total_costs, 2)} ${cur}`],
            ]}
          />
          <Ledger
            rows={[
              ["Turnover", `${plain(m.turnover)}× equity per year`],
              ["Exposure", `${Math.round(m.exposure * 100)}% invested on average`],
              ["Skipped by the no-trade band", num(m.band_skips)],
              ["Orders rejected", num(m.rejections)],
            ]}
          />
        </div>
      </Panel>
      <Panel>
        <FillsTable run={r} />
      </Panel>
    </div>
  );
}

function FillsTable({ run }: { run: RunDetail }) {
  const [all, setAll] = useState(false);
  if (run.fills.length === 0 && run.rejections.length === 0) {
    return (
      <Empty title="No trades">
        <p>The strategy never asked for a position.</p>
      </Empty>
    );
  }
  const fills = all ? run.fills : run.fills.slice(0, ROWS_SHOWN);
  return (
    <>
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
            {fills.map((f, i) => (
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
      <MoreRows shown={fills.length} total={run.fills.length} noun="fills" onShowAll={() => setAll(true)} />
    </>
  );
}

function TaxTab({ run: r }: { run: RunDetail }) {
  const a = r.analysis;
  if (!a) {
    return (
      <Panel>
        <Empty title="No tax view">
          <p>This run was saved before the tax view existed.</p>
        </Empty>
      </Panel>
    );
  }
  const buckets = Object.entries(a.holding_buckets);
  const sold = buckets.reduce((s, [, n]) => s + n, 0);
  return (
    <Panel pad>
      <h3 className="panel-title">Holding periods (Australia, information only)</h3>
      {sold === 0 ? (
        <p className="small secondary">
          {a.open_at_end ? "Nothing was sold, so every gain is still unrealised." : "No positions were bought or sold."}
        </p>
      ) : (
        <Ledger
          rows={[
            ...(buckets.map(([k, n]) => [`Sold after ${k}`, `${num(n)} position${n === 1 ? "" : "s"}`]) as [string, string][]),
            ["Share of gains held over 12 months", prob(a.long_term_gain_share)],
          ]}
        />
      )}
      <p className="small tertiary panel-foot">
        Gains on assets held over 12 months can get the CGT discount. Sells are matched to buys first-in first-out. The
        engine does not model tax, and this is not tax advice.
        {a.open_at_end && sold > 0 ? " A position was still open at the end, so part of the gain is unrealised." : ""}
      </p>
    </Panel>
  );
}

function DetailsTab({ run: r }: { run: RunDetail }) {
  return (
    <Panel pad>
      <Ledger
        rows={[
          ["Run", <span className="mono">{r.id}</span>],
          ["Created", dateTime(r.created_at)],
          ["Strategy", <span className="mono">{r.strategy}</span>],
          ["Role", runRole(r)],
          ["Parameters", <span className="mono">{paramsText(r.params)}</span>],
          ["Config fingerprint", <span className="mono">{r.config_fingerprint}</span>],
          [
            "Code",
            r.code?.commit ? (
              <span className="mono">
                {r.code.commit.slice(0, 10)}
                {r.code.dirty && <span className="warn"> + uncommitted changes</span>}
              </span>
            ) : (
              "unknown"
            ),
          ],
          [
            "Holdout",
            r.used_holdout ? <Status tone="warn">Used</Status> : <span className="secondary">Not touched</span>,
          ],
        ]}
      />
      <p className="small tertiary panel-foot">
        The full config used is saved with the run, so this result keeps its meaning if kestrel.toml changes later.
      </p>
    </Panel>
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
