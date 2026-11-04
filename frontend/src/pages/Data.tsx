import { useEffect, useState } from "react";
import { api, type Dataset, type Job } from "../api";
import { CandleChart } from "../components/CandleChart";
import { IngestForm } from "../components/IngestForm";
import { Empty, PageHeader, Panel, Section, Status } from "../components/ui";
import { date, dateTime, num } from "../format";
import { useResource } from "../hooks";

const key = (d: Pick<Dataset, "symbol" | "interval">) => `${d.symbol}/${d.interval}`;

export function Data({ jobs, onJobStarted }: { jobs: Job[]; onJobStarted: (job: Job) => void }) {
  const datasets = useResource(api.datasets, []);
  const [selected, setSelected] = useState<string>();

  // Refresh the dataset list whenever an ingest job finishes.
  const finished = jobs.filter((j) => j.kind === "ingest" && j.finished_at).length;
  useEffect(() => {
    datasets.reload();
  }, [finished, datasets.reload]);

  const list = datasets.data ?? [];
  const current = list.find((d) => key(d) === selected) ?? list[0];

  return (
    <>
      <PageHeader title="Data" subtitle="Historical Binance spot data: ingest, inspect and audit." />

      <Section title="Ingest">
        <Panel pad>
          <IngestForm onStarted={onJobStarted} />
        </Panel>
      </Section>

      <Section title="Datasets">
        <Panel>
          {list.length === 0 ? (
            <Empty title={datasets.loading ? "Loading datasets" : "No data stored yet"}>
              {!datasets.loading && <p>Ingest a dataset above. BTCUSDT 1h from 2020 is a good starting point.</p>}
            </Empty>
          ) : (
            <div className="table-wrap">
              <table className="table nowrap">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Interval</th>
                    <th>Venue</th>
                    <th className="num">Months</th>
                    <th className="num">Bars</th>
                    <th className="num">First bar</th>
                    <th className="num">Last bar</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((d) => (
                    <tr
                      key={key(d)}
                      className={`clickable${current && key(current) === key(d) ? " selected" : ""}`}
                      onClick={() => setSelected(key(d))}
                    >
                      <td style={{ fontWeight: 500 }}>{d.symbol}</td>
                      <td>{d.interval}</td>
                      <td className="secondary">Binance spot</td>
                      <td className="num">{d.months}</td>
                      <td className="num">{num(d.rows)}</td>
                      <td className="num">{date(d.first)}</td>
                      <td className="num">{date(d.last)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </Section>

      {current && <DatasetDetail ds={current} />}
    </>
  );
}

const RANGES = [
  { bars: 250, label: "250 bars" },
  { bars: 1000, label: "1,000 bars" },
  { bars: 5000, label: "5,000 bars" },
  { bars: 20000, label: "20,000 bars" },
];

function DatasetDetail({ ds }: { ds: Dataset }) {
  const [limit, setLimit] = useState(1000);
  const bars = useResource(() => api.bars(ds.symbol, ds.interval, limit), [ds.symbol, ds.interval, ds.rows, limit]);
  const quality = useResource(() => api.quality(ds.symbol, ds.interval), [ds.symbol, ds.interval, ds.rows]);
  const q = quality.data;
  const b = bars.data;

  const lastClose = b?.close.at(-1);
  const firstOpen = b?.open[0];
  const change = lastClose !== undefined && firstOpen ? lastClose / firstOpen - 1 : undefined;

  const checks = q
    ? [
        { label: "Duplicate bars", value: q.duplicates },
        { label: "Misaligned timestamps", value: q.misaligned },
        { label: "OHLC violations", value: q.ohlc_violations },
        { label: "Non-positive prices", value: q.non_positive_prices },
        { label: "Negative volumes", value: q.negative_volumes },
        { label: "Null values", value: q.nulls },
      ]
    : [];

  return (
    <Section title={`${ds.symbol} ${ds.interval}`}>
      <div className="split">
        <Panel>
          <div className="chart-meta">
            <div className="last">
              {lastClose !== undefined ? (
                <>
                  <strong>{lastClose.toLocaleString("en-AU", { maximumFractionDigits: 2 })}</strong>
                  {change !== undefined && (
                    <span className={change >= 0 ? "up" : "down"}>
                      {change >= 0 ? "+" : ""}
                      {(change * 100).toFixed(2)}%
                    </span>
                  )}{" "}
                  over the window shown
                </>
              ) : (
                "Loading bars"
              )}
            </div>
            <select
              className="select"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              aria-label="Bars to show"
            >
              {RANGES.map((r) => (
                <option key={r.bars} value={r.bars}>
                  Last {r.label}
                </option>
              ))}
            </select>
          </div>
          {b ? <CandleChart bars={b} /> : <div className="chart" />}
        </Panel>

        <Panel pad>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
            <h2>Quality audit</h2>
            {q && <Status tone={q.is_clean ? "ok" : "bad"}>{q.is_clean ? "Clean" : "Failed"}</Status>}
          </div>
          {!q ? (
            <p className="secondary">{quality.error ?? "Auditing"}</p>
          ) : (
            <>
              <dl className="dl">
                <dt>Bars checked</dt>
                <dd>{num(q.rows)}</dd>
                {checks.map((c) => (
                  <FragmentRow key={c.label} label={c.label} value={c.value} />
                ))}
                <dt>Gaps</dt>
                <dd>{q.gaps.length ? `${q.gaps.length} (${num(q.missing_bars)} bars)` : "None"}</dd>
              </dl>
              {q.gaps.length > 0 && (
                <div style={{ marginTop: 20 }}>
                  <p className="small secondary" style={{ marginBottom: 8 }}>
                    Gaps are reported, not filled. Short ones usually match real exchange outages.
                  </p>
                  <dl className="dl small">
                    {q.gaps.slice(0, 6).map((g) => (
                      <FragmentRow key={g.after} label={dateTime(g.after)} value={`${g.missing_bars} missing`} />
                    ))}
                  </dl>
                  {q.gaps.length > 6 && <p className="small tertiary">and {q.gaps.length - 6} more</p>}
                </div>
              )}
            </>
          )}
        </Panel>
      </div>
    </Section>
  );
}

function FragmentRow({ label, value }: { label: string; value: number | string }) {
  const bad = typeof value === "number" && value > 0;
  return (
    <>
      <dt>{label}</dt>
      <dd className={bad ? "bad" : ""}>{typeof value === "number" ? num(value) : value}</dd>
    </>
  );
}
