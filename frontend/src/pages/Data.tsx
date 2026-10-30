import { useEffect, useState } from "react";
import { api, type Dataset, type Job } from "../api";
import { CandleChart } from "../components/CandleChart";
import { IngestForm } from "../components/IngestForm";
import { Card, Empty, PageHeader, Pill } from "../components/ui";
import { date, dateTime, num } from "../format";
import { useResource } from "../hooks";

const key = (d: Pick<Dataset, "symbol" | "interval">) => `${d.symbol}/${d.interval}`;

export function Data({ jobs, onJobStarted }: { jobs: Job[]; onJobStarted: (job: Job) => void }) {
  const datasets = useResource(api.datasets, []);
  const [selected, setSelected] = useState<string>();

  // Refresh the dataset list whenever an ingest job finishes.
  const finished = jobs.filter((j) => j.kind === "ingest" && j.finished_at).length;
  useEffect(() => datasets.reload(), [finished, datasets.reload]);

  const list = datasets.data ?? [];
  const current = list.find((d) => key(d) === selected) ?? list[0];

  return (
    <>
      <PageHeader title="Data" subtitle="Historical market data from Binance: ingest, inspect and audit." />

      <Card title="Ingest from data.binance.vision">
        <IngestForm onStarted={onJobStarted} />
        <p className="muted" style={{ margin: "12px 0 0", fontSize: 12 }}>
          Downloads monthly archives, verifies each against Binance's SHA-256 checksum, stores them as Parquet and runs
          a quality audit. Re-ingesting a month replaces it, so it's always safe to re-run.
        </p>
      </Card>

      <div className="spacer" />

      <Card title="Datasets" flush>
        {list.length === 0 ? (
          <Empty title={datasets.loading ? "Loading…" : "No data stored yet"}>
            {!datasets.loading && <p>Start an ingest above. BTCUSDT 1h from 2020 is a good first dataset.</p>}
          </Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Interval</th>
                <th>Venue</th>
                <th className="num">Months</th>
                <th className="num">Bars</th>
                <th className="num">From</th>
                <th className="num">To</th>
              </tr>
            </thead>
            <tbody>
              {list.map((d) => (
                <tr
                  key={key(d)}
                  className={`clickable${current && key(current) === key(d) ? " selected" : ""}`}
                  onClick={() => setSelected(key(d))}
                >
                  <td className="mono">{d.symbol}</td>
                  <td className="mono">{d.interval}</td>
                  <td className="muted">{d.venue}</td>
                  <td className="num mono">{d.months}</td>
                  <td className="num mono">{num(d.rows)}</td>
                  <td className="num mono">{date(d.first)}</td>
                  <td className="num mono">{date(d.last)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {current && <DatasetDetail ds={current} />}
    </>
  );
}

function DatasetDetail({ ds }: { ds: Dataset }) {
  const [limit, setLimit] = useState(1000);
  const bars = useResource(() => api.bars(ds.symbol, ds.interval, limit), [ds.symbol, ds.interval, ds.rows, limit]);
  const quality = useResource(() => api.quality(ds.symbol, ds.interval), [ds.symbol, ds.interval, ds.rows]);
  const q = quality.data;

  return (
    <>
      <div className="spacer" />
      <div className="grid two-col">
        <Card
          title={
            <>
              {ds.symbol} <span className="muted mono">· {ds.interval}</span>
            </>
          }
          actions={
            <div className="field">
              <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} aria-label="Bars to show">
                {[250, 1000, 5000, 20000].map((n) => (
                  <option key={n} value={n}>
                    Last {num(n)} bars
                  </option>
                ))}
              </select>
            </div>
          }
        >
          {bars.data ? <CandleChart bars={bars.data} /> : <div className="chart" />}
        </Card>

        <Card
          title="Data quality"
          actions={q && <Pill tone={q.is_clean ? "ok" : "bad"}>{q.is_clean ? "clean" : "failed"}</Pill>}
        >
          {!q ? (
            <div className="muted">{quality.error ?? "Auditing…"}</div>
          ) : (
            <>
              <dl className="kv">
                <dt>Bars</dt>
                <dd>{num(q.rows)}</dd>
                <dt>Duplicates</dt>
                <dd className={q.duplicates ? "bad" : ""}>{q.duplicates}</dd>
                <dt>Misaligned timestamps</dt>
                <dd className={q.misaligned ? "bad" : ""}>{q.misaligned}</dd>
                <dt>OHLC violations</dt>
                <dd className={q.ohlc_violations ? "bad" : ""}>{q.ohlc_violations}</dd>
                <dt>Non-positive prices</dt>
                <dd className={q.non_positive_prices ? "bad" : ""}>{q.non_positive_prices}</dd>
                <dt>Negative volumes</dt>
                <dd className={q.negative_volumes ? "bad" : ""}>{q.negative_volumes}</dd>
                <dt>Nulls</dt>
                <dd className={q.nulls ? "bad" : ""}>{q.nulls}</dd>
                <dt>Gaps</dt>
                <dd>
                  {q.gaps.length} ({num(q.missing_bars)} bars)
                </dd>
              </dl>
              {q.gaps.length > 0 && (
                <>
                  <div className="spacer" />
                  <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                    Gaps are reported, not filled. Small ones usually match real exchange outages.
                  </div>
                  <dl className="kv" style={{ fontSize: 12 }}>
                    {q.gaps.slice(0, 8).map((g) => (
                      <div key={g.after} style={{ display: "contents" }}>
                        <dt className="mono">{dateTime(g.after)}</dt>
                        <dd>{g.missing_bars} bars</dd>
                      </div>
                    ))}
                  </dl>
                  {q.gaps.length > 8 && <div className="muted" style={{ fontSize: 12 }}>+{q.gaps.length - 8} more</div>}
                </>
              )}
            </>
          )}
        </Card>
      </div>
    </>
  );
}
