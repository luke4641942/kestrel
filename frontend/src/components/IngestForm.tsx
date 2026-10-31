import { useState, type FormEvent } from "react";
import { INTERVALS, api, type Interval, type Job } from "../api";
import { lastCompleteMonth } from "../format";

export function IngestForm({ onStarted }: { onStarted: (job: Job) => void }) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setInterval] = useState<Interval>("1h");
  const [start, setStart] = useState("2020-01");
  const [end, setEnd] = useState(lastCompleteMonth());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      onStarted(await api.ingest({ symbol: symbol.trim().toUpperCase(), interval, start, end: end || undefined }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="form">
        <div className="field">
          <label htmlFor="sym">Pair</label>
          <input id="sym" value={symbol} onChange={(e) => setSymbol(e.target.value)} spellCheck={false} />
        </div>
        <div className="field">
          <label htmlFor="int">Interval</label>
          <select id="int" value={interval} onChange={(e) => setInterval(e.target.value as Interval)}>
            {INTERVALS.map((i) => (
              <option key={i}>{i}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="start">From month</label>
          <input id="start" type="month" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="end">To month</label>
          <input id="end" type="month" value={end} max={lastCompleteMonth()} onChange={(e) => setEnd(e.target.value)} />
        </div>
        <button className="btn btn-primary" disabled={busy || !symbol || !start}>
          {busy ? "Starting" : "Start ingest"}
        </button>
      </div>
      {error ? (
        <div className="form-error">{error}</div>
      ) : (
        <p className="form-hint">
          Downloads monthly archives from data.binance.vision, verifies each checksum and runs a quality audit.
          Re-ingesting a month replaces it.
        </p>
      )}
    </form>
  );
}
