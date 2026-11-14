import { useEffect, useState, type FormEvent } from "react";
import { api, type Job, type RegimeDetail } from "../api";
import { RegimeChart } from "../components/RegimeChart";
import { Empty, PageHeader, Panel, Section } from "../components/ui";
import { date } from "../format";
import { useResource } from "../hooks";
import { firstFitted, latestReading, stateColor, stateName } from "../regimes";

const pct = (x: number | null, digits = 0) => (x === null ? "n/a" : `${(x * 100).toFixed(digits)}%`);
const signed = (x: number | null) => (x === null ? "n/a" : `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(2)}%`);
const days = (x: number | null) => (x === null ? "n/a" : `${Math.round(x)} days`);

// Everything shown comes from the API's stored fit (kestrel.regimes); the page never
// fits or re-labels anything itself. It describes states; it never trades them.
export function Regimes({ jobs, onJobStarted }: { jobs: Job[]; onJobStarted: (job: Job) => void }) {
  const datasets = useResource(api.datasets, []);
  const fits = useResource(api.regimes, []);
  const daily = (datasets.data ?? []).filter((d) => d.interval === "1d").map((d) => d.symbol);
  const [chosen, setChosen] = useState<string>();
  const symbol = chosen ?? fits.data?.[0]?.symbol ?? (daily.includes("BTCUSDT") ? "BTCUSDT" : daily[0]);
  const fitted = (fits.data ?? []).some((f) => f.symbol === symbol);

  // A finished fit refreshes the list and the chart.
  const finished = jobs.filter((j) => j.kind === "regimes" && j.finished_at).length;
  useEffect(() => {
    fits.reload();
  }, [finished, fits.reload]);

  return (
    <>
      <PageHeader
        title="Regimes"
        subtitle="Hidden market states from a hidden Markov model, as the model saw them at the time."
      />
      <Section title="Fit a market">
        <FitForm markets={daily} symbol={symbol} fitted={fitted} onChoose={setChosen} onStarted={onJobStarted} />
      </Section>
      {symbol && fitted ? (
        <RegimeView symbol={symbol} version={`${finished}`} />
      ) : (
        <Panel>
          <Empty title={symbol ? `${symbol} hasn't been fitted yet` : "No daily data yet"}>
            <p>
              {symbol
                ? "Fit it above. Each month's model is fitted on the four years before it, so a market needs about four years of daily bars."
                : "Ingest a market at 1d on the Data page first."}
            </p>
          </Empty>
        </Panel>
      )}
    </>
  );
}

function FitForm({
  markets,
  symbol,
  fitted,
  onChoose,
  onStarted,
}: {
  markets: string[];
  symbol: string | undefined;
  fitted: boolean;
  onChoose: (s: string) => void;
  onStarted: (job: Job) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!symbol) return;
    setBusy(true);
    setError(undefined);
    api
      .startRegimes(symbol)
      .then(onStarted)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <form onSubmit={submit}>
      <div className="form form-regimes">
        <div className="field">
          <label htmlFor="rg-market">Market (daily bars)</label>
          <select id="rg-market" value={symbol ?? ""} onChange={(e) => onChoose(e.target.value)}>
            {markets.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <button className="btn btn-primary" disabled={busy || !symbol}>
          {busy ? "Starting" : fitted ? "Refit" : "Fit regimes"}
        </button>
      </div>
      {error ? (
        <div className="form-error">{error}</div>
      ) : (
        <p className="form-hint">
          Refitted every month on the four years before it, using data before the holdout only. Takes a minute or two;
          progress shows under Jobs. Refitting replaces the saved fit.
        </p>
      )}
    </form>
  );
}

function RegimeView({ symbol, version }: { symbol: string; version: string }) {
  const detail = useResource(() => api.regime(symbol), [symbol, version]);
  const d = detail.data;
  if (detail.error) {
    return (
      <Panel>
        <Empty title="Couldn't load the fit">
          <p>{detail.error}</p>
        </Empty>
      </Panel>
    );
  }
  if (!d || d.symbol !== symbol) return null;
  const n = d.states.length;
  const reading = latestReading(d);
  const from = firstFitted(d);
  const fromDay = from === null ? "n/a" : date(new Date(from * 1000).toISOString());
  return (
    <Section
      title={`${d.symbol} · ${n} states`}
      description={`States from ${fromDay} to ${date(d.last)}: ${d.refits} monthly fit${d.refits === 1 ? "" : "s"}, each on the four years before it (data from ${date(d.first)}). Fitted ${date(d.created_at)}.`}
    >
      <div className="run-stack">
        {reading && (
          <p className="regime-reading">
            On {date(new Date(reading.time * 1000).toISOString())} the model put {d.symbol} in{" "}
            <StateLabel state={reading.state} states={n} /> ({pct(reading.probability)} likely).
          </p>
        )}
        <Panel>
          <div className="regime-legend" aria-label="States">
            {d.states.map((s) => (
              <StateLabel key={s.state} state={s.state} states={n} />
            ))}
            <span className="small tertiary">Shading: the most likely state that day. Below: each state's probability.</span>
          </div>
          <RegimeChart detail={d} />
        </Panel>
        <StateCards detail={d} />
        {d.transitions && <Switching detail={d} transitions={d.transitions} />}
        <p className="small tertiary regime-note">
          Every day's state is what the model believed that day, from a model fitted only on earlier data, so nothing here
          uses the future. States are sorted by volatility after every fit, calmest first. Returns are measured on the
          same days the model calls a state, and big moves are part of what it sees, so they describe the states rather
          than predict them. A strategy that trades on these states needs its own research note and the scorecard.
        </p>
      </div>
    </Section>
  );
}

function StateLabel({ state, states }: { state: number; states: number }) {
  return (
    <span className="regime-label">
      <span className="regime-swatch" style={{ background: `var(${stateColor(state)})` }} aria-hidden="true" />
      {stateName(state, states)}
    </span>
  );
}

function StateCards({ detail: d }: { detail: RegimeDetail }) {
  return (
    <div className="regime-cards">
      {d.states.map((s) => (
        <Panel key={s.state} pad>
          <h3 className="regime-card-title">
            <StateLabel state={s.state} states={d.states.length} />
          </h3>
          <dl className="regime-facts">
            <dt>Share of days</dt>
            <dd>{pct(s.share)}</dd>
            <dt>Volatility</dt>
            <dd>{pct(s.volatility)} a year</dd>
            <dt>Return per day</dt>
            <dd>{signed(s.mean_return)}</dd>
            <dt>Average spell</dt>
            <dd>{days(s.average_run)}</dd>
            <dt>Expected stay</dt>
            <dd>{days(s.expected_days)}</dd>
          </dl>
        </Panel>
      ))}
    </div>
  );
}

function Switching({ detail: d, transitions }: { detail: RegimeDetail; transitions: number[][] }) {
  const n = d.states.length;
  return (
    <Panel>
      <div className="table-wrap">
        <table className="table regime-switching">
          <caption className="small secondary">
            Switching odds from the latest model: the chance of each state tomorrow, given today's.
          </caption>
          <thead>
            <tr>
              <th>Today</th>
              {transitions.map((_, j) => (
                <th key={j} className="num">
                  Tomorrow {stateName(j, n).toLowerCase()}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {transitions.map((row, i) => (
              <tr key={i}>
                <td>
                  <StateLabel state={i} states={n} />
                </td>
                {row.map((p, j) => (
                  <td key={j} className={`num${i === j ? " regime-stay" : ""}`}>
                    {pct(p, 1)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
