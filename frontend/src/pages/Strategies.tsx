import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { MarketBadge, useMarket } from "../market";
import { classOf, inMarket } from "../markets";
import {
  api,
  type Dataset,
  type Job,
  type Note,
  type ResearchDetail,
  type ResearchSummary,
  type StrategyInfo,
} from "../api";
import { ParamInputs, defaultParams } from "../components/ParamInputs";
import { IndicatorPicker, useIndicatorSelection } from "../components/IndicatorPicker";
import { SignalsChart } from "../components/SignalsChart";
import { Empty, FoldToggle, PageHeader, Panel, Status } from "../components/ui";
import { ago, dateTime, lastResearchMonth } from "../format";
import {
  KIND_LABEL,
  RESEARCH_STATUS as STATUS,
  defaultDataset,
  marketLabel,
  marketsFor,
  signalsCaption,
  isSettled,
  splitClosed,
} from "../summary";
import { useResource } from "../hooks";



/** The slug in `#/strategies/<slug>`, if any. */
function slugFromHash(): string | undefined {
  const [, slug] = window.location.hash.replace(/^#\/?/, "").split("/");
  return slug || undefined;
}

export function Strategies({ jobs, onJobStarted }: { jobs: Job[]; onJobStarted: (job: Job) => void }) {
  const list = useResource(api.researchList, [], undefined, true);
  const [slug, setSlug] = useState<string | undefined>(slugFromHash);

  useEffect(() => {
    const onHash = () => setSlug(slugFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const select = (s: string) => {
    window.location.hash = `/strategies/${s}`;
  };
  const { market } = useMarket();
  // A draft with no implementation yet belongs to no market: it shows under every one.
  const items = (list.data ?? []).filter((s) => s.implementation === null || inMarket(s, market));
  const { live, closed } = splitClosed(items, (s) => isSettled(s.status));
  const current = slug ?? live[0]?.slug ?? items[0]?.slug;
  // Rejected ideas are folded away, but one that is open stays visible in the list.
  const [showRejected, setShowRejected] = useState(false);
  const currentRejected = closed.some((s) => s.slug === current);
  useEffect(() => {
    if (currentRejected) setShowRejected(true);
  }, [currentRejected]);

  return (
    <>
      <PageHeader
        title="Strategies"
        subtitle="Write the hypothesis down, freeze it, then test it. Every run is logged as a trial against the version it tested."
      />
      <div className="strat-layout">
        <aside className="strat-list">
          <NewIdea
            onCreated={(s) => {
              list.reload();
              select(s);
            }}
          />
          <Panel>
            {items.length === 0 ? (
              <Empty title={list.loading ? "Loading" : "No strategies yet"}>
                {!list.loading && <p>Start with an idea above. It stays a draft until you freeze it.</p>}
              </Empty>
            ) : (
              <>
                <ul className="strat-items">
                  {(showRejected ? [...live, ...closed] : live).map((s) => (
                    <StrategyItem key={s.slug} s={s} active={s.slug === current} onSelect={() => select(s.slug)} />
                  ))}
                </ul>
                <FoldToggle
                  count={closed.length}
                  noun={`rejected or validated idea${closed.length === 1 ? "" : "s"}`}
                  open={showRejected}
                  onToggle={() => setShowRejected((v) => !v)}
                />
              </>
            )}
          </Panel>
        </aside>
        {current ? (
          <Workspace key={current} slug={current} jobs={jobs} onJobStarted={onJobStarted} onChanged={list.reload} />
        ) : (
          <div className="strat-empty">
            <Panel pad>
              <Empty title="Nothing selected">
                <p>
                  A strategy here is a research note first: what you're betting on, why the edge should exist, the
                  rules in plain English, what would prove it wrong, and how often it trades against costs.
                </p>
              </Empty>
            </Panel>
          </div>
        )}
      </div>
    </>
  );
}

function StrategyItem({ s, active, onSelect }: { s: ResearchSummary; active: boolean; onSelect: () => void }) {
  return (
    <li>
      <button type="button" className={`strat-item${active ? " active" : ""}`} onClick={onSelect}>
        <span className="strat-item-name">
          {s.name} <MarketBadge cls={classOf(s)} />
        </span>
        <span className="strat-item-meta">
          <Status tone={STATUS[s.status].tone}>
            <span className="small secondary">{STATUS[s.status].label}</span>
          </Status>
          <span className="small tertiary">
            v{s.version}
            {s.frozen ? "" : " draft"} · {s.trials} trial{s.trials === 1 ? "" : "s"}
          </span>
        </span>
      </button>
    </li>
  );
}

function NewIdea({ onCreated }: { onCreated: (slug: string) => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string>();
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(undefined);
    try {
      const created = await api.createResearch(name.trim());
      setName("");
      onCreated(created.slug);
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <form className="new-idea" onSubmit={submit}>
      <label htmlFor="new-idea" className="cost-label">
        New idea
      </label>
      <div className="row">
        <input
          id="new-idea"
          placeholder="e.g. BTC 4h trend"
          value={name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
        />
        <button className="btn btn-primary btn-sm" disabled={!name.trim()}>
          Add
        </button>
      </div>
      {error && <div className="form-error">{error}</div>}
    </form>
  );
}

function Workspace({
  slug,
  jobs,
  onJobStarted,
  onChanged,
}: {
  slug: string;
  jobs: Job[];
  onJobStarted: (job: Job) => void;
  onChanged: () => void;
}) {
  const detail = useResource(() => api.research(slug), [slug], undefined, true);
  const strategies = useResource(api.strategies, []);
  const [tab, setTab] = useState<"assistant" | "trials">("assistant");
  const [override, setOverride] = useState<ResearchDetail>();
  const [viewVersion, setViewVersion] = useState<number>();

  // Trial log refreshes when a backtest finishes.
  const finished = jobs.filter((j) => j.kind === "backtest" && j.finished_at).length;
  useEffect(() => {
    setOverride(undefined);
    detail.reload();
  }, [finished, detail.reload]);

  const d = override ?? detail.data;
  if (detail.error) {
    return (
      <div className="strat-main">
        <Panel pad>
          <Empty title="Couldn't load this strategy">
            <p>{detail.error}</p>
          </Empty>
        </Panel>
      </div>
    );
  }
  if (!d) return <div className="strat-main" />;

  const updated = (next: ResearchDetail) => {
    setOverride(next);
    setViewVersion(undefined);
    onChanged();
  };
  const latest = d.notes[d.notes.length - 1]!;
  const shown = d.notes.find((n) => n.version === viewVersion) ?? latest;
  const isLatest = shown.version === latest.version;
  const editable = isLatest && !shown.frozen_at && d.status !== "rejected";
  const impl = strategies.data?.find((s) => s.key === shown.implementation);

  return (
    <>
      <section className="strat-main" aria-label={d.name}>
        <div className="strat-head">
          <div>
            <h2 className="strat-title">{d.name}</h2>
            <div className="row small secondary">
              <Status tone={STATUS[d.status].tone}>{STATUS[d.status].label}</Status>
              <span>
                {d.runs} run{d.runs === 1 ? "" : "s"} · {d.trials} distinct trial{d.trials === 1 ? "" : "s"}
              </span>
            </div>
          </div>
          {d.notes.length > 1 && (
            <div className="seg" role="group" aria-label="Note version">
              {d.notes.map((n) => (
                <button
                  key={n.version}
                  type="button"
                  aria-pressed={n.version === shown.version}
                  onClick={() => setViewVersion(n.version)}
                >
                  v{n.version}
                </button>
              ))}
            </div>
          )}
        </div>

        <Panel pad>
          <NoteHeader note={shown} isLatest={isLatest} />
          {editable ? (
            <NoteEditor
              key={`${slug}-${shown.version}`}
              slug={slug}
              note={shown}
              headings={d.sections}
              strategies={strategies.data ?? []}
              onSaved={updated}
              extra={<RejectAction slug={slug} onDone={updated} />}
            />
          ) : (
            <NoteView note={shown} headings={d.sections} />
          )}
          {isLatest && shown.frozen_at && d.status !== "rejected" && <VersionActions slug={slug} onDone={updated} />}
        </Panel>

        {impl && (
          <>
            <CodeView impl={impl} />
            <SignalsPanel impl={impl} />
            {isLatest && shown.frozen_at && d.status !== "rejected" && (
              <TrialRunner slug={slug} note={shown} impl={impl} onJobStarted={onJobStarted} />
            )}
          </>
        )}
        {!impl && (
          <Panel pad>
            <p className="secondary small">
              Choose the implementation this note will be tested with to see its code and the signals it gives.
              Strategy code lives in the repo; a research note can only be backtested once it is frozen.
            </p>
          </Panel>
        )}
      </section>

      <aside className="strat-side">
        <div className="seg strat-tabs" role="tablist">
          <button type="button" role="tab" aria-pressed={tab === "assistant"} onClick={() => setTab("assistant")}>
            Assistant
          </button>
          <button type="button" role="tab" aria-pressed={tab === "trials"} onClick={() => setTab("trials")}>
            Trial log ({d.trials})
          </button>
        </div>
        {tab === "assistant" ? <AssistantPanel d={d} onReply={setOverride} /> : <TrialLog d={d} />}
      </aside>
    </>
  );
}

function NoteHeader({ note, isLatest }: { note: Note; isLatest: boolean }) {
  return (
    <div className="note-head" data-tour="note">
      <h3>
        Research note v{note.version}
        {!isLatest && <span className="tertiary"> (superseded)</span>}
      </h3>
      {note.frozen_at ? (
        <span className="chip frozen-chip" title="Frozen notes can't be edited">
          Frozen {dateTime(note.frozen_at)}
        </span>
      ) : (
        <span className="chip assumption">Draft</span>
      )}
      {note.change_reason && <p className="note-reason">Why this version: {note.change_reason}</p>}
    </div>
  );
}

function NoteView({ note, headings }: { note: Note; headings: Record<string, string> }) {
  return (
    <div className="note-sections">
      {Object.entries(headings).map(([key, heading]) => (
        <div key={key} className="note-section">
          <h4>{heading}</h4>
          <p>{note.sections[key] || <span className="tertiary">Not written.</span>}</p>
        </div>
      ))}
      <div className="note-section">
        <h4>Implementation</h4>
        <p className="mono">{note.implementation ?? "none"}</p>
      </div>
    </div>
  );
}

function NoteEditor({
  slug,
  note,
  headings,
  strategies,
  onSaved,
  extra,
}: {
  slug: string;
  note: Note;
  headings: Record<string, string>;
  strategies: StrategyInfo[];
  onSaved: (d: ResearchDetail) => void;
  /** Further actions for the same row, such as rejecting the idea. */
  extra?: ReactNode;
}) {
  const [sections, setSections] = useState(note.sections);
  const [impl, setImpl] = useState<string>(note.implementation ?? "");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const dirty =
    impl !== (note.implementation ?? "") || Object.keys(headings).some((k) => sections[k] !== note.sections[k]);
  const complete = Object.keys(headings).every((k) => sections[k]?.trim()) && impl !== "";

  async function run(fn: () => Promise<ResearchDetail>) {
    setBusy(true);
    setError(undefined);
    try {
      onSaved(await fn());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }
  const save = () => api.editNote(slug, note.version, sections, impl || null);

  return (
    <div className="note-sections">
      {Object.entries(headings).map(([key, heading]) => (
        <div key={key} className="field note-section">
          <label htmlFor={`note-${key}`}>{heading}</label>
          <textarea
            id={`note-${key}`}
            rows={key === "rules" ? 5 : 3}
            value={sections[key] ?? ""}
            onChange={(e) => setSections({ ...sections, [key]: e.target.value })}
          />
        </div>
      ))}
      <div className="field note-section">
        <label htmlFor="note-impl">Implementation it will be tested with</label>
        <select id="note-impl" value={impl} onChange={(e) => setImpl(e.target.value)}>
          <option value="">Choose…</option>
          {strategies.map((s) => (
            <option key={s.key} value={s.key}>
              {s.key} ({KIND_LABEL[s.kind]})
            </option>
          ))}
        </select>
      </div>
      <div className="note-actions">
        <button type="button" className="btn btn-sm" disabled={busy || !dirty} onClick={() => run(save)}>
          Save draft
        </button>
        {confirming ? (
          <>
            <span className="small warn">Frozen notes can't be edited. Changes later need a new version.</span>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  if (dirty) await save();
                  return api.freezeNote(slug, note.version);
                })
              }
            >
              Freeze v{note.version}
            </button>
            <button type="button" className="link-btn" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </>
        ) : (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy || !complete}
            title={complete ? "" : "Fill in every section and choose an implementation first"}
            onClick={() => setConfirming(true)}
          >
            Freeze…
          </button>
        )}
        {!confirming && extra && <div className="note-actions-end">{extra}</div>}
      </div>
      {error && <div className="form-error">{error}</div>}
    </div>
  );
}

function ReasonAction({
  label,
  placeholder,
  confirm,
  danger,
  onSubmit,
}: {
  label: string;
  placeholder: string;
  confirm: string;
  danger?: boolean;
  onSubmit: (reason: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string>();
  if (!open) {
    return (
      <button type="button" className={`btn btn-sm${danger ? " btn-danger" : ""}`} onClick={() => setOpen(true)}>
        {label}
      </button>
    );
  }
  return (
    <form
      className="reason-form"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          await onSubmit(reason.trim());
        } catch (err) {
          setError((err as Error).message);
        }
      }}
    >
      <input
        aria-label={label}
        placeholder={placeholder}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        autoFocus
      />
      <button className={`btn btn-sm${danger ? " btn-danger" : " btn-primary"}`} disabled={!reason.trim()}>
        {confirm}
      </button>
      <button type="button" className="link-btn" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {error && <div className="form-error">{error}</div>}
    </form>
  );
}

function VersionActions({ slug, onDone }: { slug: string; onDone: (d: ResearchDetail) => void }) {
  return (
    <div className="note-actions">
      <ReasonAction
        label="New version…"
        placeholder="Why is the idea changing? This is kept on the record."
        confirm="Start new version"
        onSubmit={async (r) => onDone(await api.newVersion(slug, r))}
      />
      <RejectAction slug={slug} onDone={onDone} />
    </div>
  );
}

function RejectAction({ slug, onDone }: { slug: string; onDone: (d: ResearchDetail) => void }) {
  return (
    <ReasonAction
      label="Reject…"
      placeholder="Why was it rejected? Rejections are kept as evidence."
      confirm="Reject"
      danger
      onSubmit={async (r) => onDone(await api.rejectResearch(slug, r))}
    />
  );
}

function CodeView({ impl }: { impl: StrategyInfo }) {
  const source = useResource(() => api.source(impl.key), [impl.key]);
  return (
    <Panel>
      <div className="code-head">
        <h3 className="panel-title">
          Code: <span className="mono">{impl.key}</span>{" "}
          <span className="chip">{KIND_LABEL[impl.kind]}</span>
        </h3>
        {source.data && (
          <span className="small tertiary mono">
            {source.data.commit?.slice(0, 10) ?? "no commit"}
            {source.data.dirty && <span className="warn"> + uncommitted</span>}
          </span>
        )}
      </div>
      <p className="small secondary code-desc">{impl.description} Read-only: this is exactly what the backtester runs.</p>
      <pre className="code">{source.data?.source ?? source.error ?? "Loading…"}</pre>
    </Panel>
  );
}

function useMarkets() {
  const datasets = useResource(api.datasets, []);
  const config = useResource(api.costs, []);
  const maxMonth = config.data ? lastResearchMonth(config.data.backtest.holdout_start) : undefined;
  return { datasets: datasets.data ?? [], maxMonth };
}

const dsKey = (d: Pick<Dataset, "symbol" | "interval">) => `${d.symbol}/${d.interval}`;

function rangeFor(ds: Dataset | undefined, maxMonth: string | undefined): [string, string] | undefined {
  if (!ds?.first || !ds.last || !maxMonth) return undefined;
  const last = ds.last.slice(0, 7);
  return [ds.first.slice(0, 7), last < maxMonth ? last : maxMonth];
}

function SignalsPanel({ impl }: { impl: StrategyInfo }) {
  if (impl.universe) {
    return (
      <Panel pad>
        <h3 className="panel-title">Signals</h3>
        <p className="small secondary">
          This strategy ranks every coin in the universe each week, so it has no single-market chart. Its holdings and
          trades appear in a backtest.
        </p>
      </Panel>
    );
  }
  return <MarketSignals impl={impl} />;
}

function MarketSignals({ impl }: { impl: StrategyInfo }) {
  const { datasets, maxMonth } = useMarkets();
  const [market, setMarket] = useState<string>();
  const [params, setParams] = useState(() => defaultParams(impl.params));
  const ds = datasets.find((d) => dsKey(d) === market) ?? defaultDataset(datasets);
  const range = rangeFor(ds, maxMonth);
  const [debounced, setDebounced] = useState(params);
  const timer = useRef<number>();
  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setDebounced(params), 300);
  }, [params]);

  const ind = useIndicatorSelection("kestrel.indicators.signals");
  const signals = useResource(
    () =>
      ds && range
        ? api.signals(impl.key, {
            symbol: ds.symbol,
            interval: ds.interval,
            start: range[0],
            end: range[1],
            params: debounced,
            indicators: ind.active,
          })
        : Promise.resolve(undefined),
    [impl.key, ds?.symbol, ds?.interval, range?.[0], range?.[1], JSON.stringify(debounced), ind.active.join()],
  );

  return (
    <Panel pad>
      <div className="code-head">
        <h3 className="panel-title">Signals</h3>
        <span className="small tertiary">
          {signals.data?.kind === "entries"
            ? "Arrows mark entry signals; exits depend on stops and targets, so see them in a backtest"
            : "Target weight after each bar closes; arrows mark where it would trade"}
        </span>
      </div>
      {datasets.length === 0 ? (
        <p className="secondary small">Ingest a dataset first (More → Data).</p>
      ) : (
        <>
          <div className="signals-controls">
            <div className="field">
              <label htmlFor="sig-market">Market</label>
              <select id="sig-market" value={ds ? dsKey(ds) : ""} onChange={(e) => setMarket(e.target.value)}>
                {datasets.map((d) => (
                  <option key={dsKey(d)} value={dsKey(d)}>
                    {d.symbol} {d.interval}
                  </option>
                ))}
              </select>
            </div>
            <ParamInputs params={impl.params} values={params} onChange={setParams} idPrefix="sig" />
          </div>
          <IndicatorPicker menu={ind.menu} selected={ind.active} onChange={ind.change} />
          {signals.data ? (
            <SignalsChart signals={signals.data} menu={ind.menu} indicators={ind.active} />
          ) : (
            <p className="secondary small">{signals.error ?? "Loading…"}</p>
          )}
          {range && (
            <p className="small tertiary">
              {signalsCaption(range[0], range[1], signals.data?.time.length ?? 0, signals.data?.total_bars ?? 0, signals.data?.time[0])}
            </p>
          )}
        </>
      )}
    </Panel>
  );
}

function TrialRunner({
  slug,
  note,
  impl,
  onJobStarted,
}: {
  slug: string;
  note: Note;
  impl: StrategyInfo;
  onJobStarted: (job: Job) => void;
}) {
  const { datasets, maxMonth } = useMarkets();
  const [market, setMarket] = useState<string>();
  const [params, setParams] = useState(() => defaultParams(impl.params));
  const [cash, setCash] = useState("330");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [started, setStarted] = useState(false);
  const markets = marketsFor(impl, datasets);
  const ds = markets.find((d) => dsKey(d) === market) ?? defaultDataset(markets);
  const range = rangeFor(ds, maxMonth);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ds || !range) return;
    setBusy(true);
    setError(undefined);
    try {
      onJobStarted(
        await api.startBacktest({
          symbol: ds.symbol,
          interval: ds.interval,
          strategy: impl.key,
          start: range[0],
          end: range[1],
          initial_cash: Number(cash),
          params,
          research: slug,
        }),
      );
      setStarted(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel pad>
      <h3 className="panel-title">Run as a trial of v{note.version}</h3>
      <form onSubmit={submit}>
        <div className="signals-controls">
          <div className="field">
            <label htmlFor="trial-market">Market</label>
            <select id="trial-market" value={ds ? dsKey(ds) : ""} onChange={(e) => setMarket(e.target.value)}>
              {markets.map((d) => (
                <option key={dsKey(d)} value={dsKey(d)}>
                  {marketLabel(d)}
                </option>
              ))}
            </select>
          </div>
          <ParamInputs params={impl.params} values={params} onChange={setParams} idPrefix="trial" />
          <div className="field">
            <label htmlFor="trial-cash">Cash (USDT)</label>
            <input id="trial-cash" type="number" min={1} step="any" value={cash} onChange={(e) => setCash(e.target.value)} />
          </div>
          <button className="btn btn-primary" disabled={busy || !ds || !range || !(Number(cash) > 0)}>
            {busy ? "Starting" : "Run trial"}
          </button>
        </div>
      </form>
      {error ? (
        <div className="form-error">{error}</div>
      ) : (
        <p className="form-hint">
          {started
            ? "Started. It appears in the trial log when the job finishes."
            : `Runs ${range ? `${range[0]} to ${range[1]}` : "the dataset's history"} and is logged against v${note.version}. Changing a parameter is a new trial; rerunning an identical one is not.`}
        </p>
      )}
    </Panel>
  );
}

function AssistantPanel({ d, onReply }: { d: ResearchDetail; onReply: (d: ResearchDetail) => void }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [d.chat.length, busy]);

  async function send(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text) return;
    setBusy(true);
    setError(undefined);
    try {
      onReply(await api.chat(d.slug, text));
      setMessage("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel>
      <p className="assistant-note small">
        The assistant sees this note and its code, <strong>never backtest results</strong>, so it can't tune the
        strategy to past data. Conversations are saved with the strategy.
      </p>
      {!d.assistant_available && <p className="small secondary assistant-pad">The assistant isn't enabled on this server.</p>}
      {d.assistant_hint && <div className="assistant-hint small">{d.assistant_hint}</div>}
      <div className="chat">
        {d.chat.length === 0 && !busy && (
          <p className="small tertiary">
            Try: "Who loses money to this strategy?" or "Estimate its turnover against a 22 bps round trip."
          </p>
        )}
        {d.chat.map((m, i) => (
          <div key={i} className={`chat-msg ${m.role}`}>
            <div className="chat-meta small tertiary">
              {m.role === "user" ? "You" : "Assistant"} · v{m.note_version} · {ago(m.at)}
            </div>
            <div className="chat-text">{m.content}</div>
          </div>
        ))}
        {busy && <div className="chat-msg assistant small tertiary">Thinking…</div>}
        <div ref={end} />
      </div>
      <form className="chat-form" onSubmit={send}>
        <textarea
          aria-label="Message the research assistant"
          rows={3}
          placeholder="Ask about the hypothesis, rules, costs or code…"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send(e);
          }}
          disabled={!d.assistant_available}
        />
        <div className="row chat-actions">
          <span className="small tertiary">⌘↵ to send</span>
          <button className="btn btn-primary btn-sm" disabled={busy || !message.trim() || !d.assistant_available}>
            Send
          </button>
        </div>
        {error && <div className="form-error">{error}</div>}
      </form>
    </Panel>
  );
}

function TrialLog({ d }: { d: ResearchDetail }) {
  if (d.trial_log.length === 0) {
    return (
      <Panel pad>
        <Empty title="No trials yet">
          <p>Freeze the note, then run it as a trial. Every run is counted here against the version it tested.</p>
        </Empty>
      </Panel>
    );
  }
  return (
    <Panel>
      <p className="assistant-note small">
        {d.runs} run{d.runs === 1 ? "" : "s"}, <strong>{d.trials} distinct trial{d.trials === 1 ? "" : "s"}</strong>.
        The more variants tried, the more a good result is likely to be luck. P3's deflated Sharpe corrects for this
        count.
      </p>
      <div className="table-wrap">
        <table className="table nowrap trial-table">
          <thead>
            <tr>
              <th>Ver</th>
              <th>Params</th>
              <th>Market</th>
              <th className="num">Return</th>
              <th className="num">Sharpe</th>
            </tr>
          </thead>
          <tbody>
            {[...d.trial_log].reverse().map((r) => (
              <tr key={r.id} className="clickable" onClick={() => (window.location.hash = `/backtests/${r.id}`)}>
                <td>v{r.research?.version}</td>
                <td className="mono small">
                  {Object.entries(r.params)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(" ") || "default"}
                </td>
                <td className="small">
                  {r.symbol} {r.interval}
                  <div className="cell-sub">
                    {r.start} to {r.end}
                  </div>
                </td>
                <td className="num">{`${r.metrics.total_return >= 0 ? "+" : ""}${(r.metrics.total_return * 100).toFixed(1)}%`}</td>
                <td className="num">{r.metrics.sharpe === null ? "n/a" : r.metrics.sharpe.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
