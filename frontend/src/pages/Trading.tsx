import { useState } from "react";
import { api, type PaperDetail, type PaperSummary } from "../api";
import { EquityChart } from "../components/EquityChart";
import { Empty, PageHeader, Panel, Section, Stat, Status } from "../components/ui";
import { date, dateTime } from "../format";
import { useResource } from "../hooks";
import { trackingVerdict, venueLabel } from "../paperView";

const usd = (x: number | null) => (x === null ? "n/a" : x.toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const pct = (x: number) => `${(x * 100).toFixed(0)}%`;

// Read-only by design: paper accounts are opened, run, killed and resumed from the CLI
// (kestrel paper ..., kestrel kill), where every action is recorded in the account's ledger.
export function Trading() {
  const accounts = useResource(api.paper, [], 60_000);
  const list = accounts.data ?? [];
  const [chosen, setChosen] = useState<string>();
  const current = list.find((a) => a.name === chosen) ?? list[0];

  return (
    <>
      <PageHeader
        title="Trading"
        subtitle="Paper accounts running forward, through the same code as the backtest, against what the backtest model says should have happened."
      />
      {accounts.error ? (
        <Panel>
          <Empty title="Couldn't load the paper accounts">
            <p>{accounts.error}</p>
          </Empty>
        </Panel>
      ) : list.length === 0 ? (
        <Panel pad>
          <Empty title={accounts.loading ? "Loading paper accounts" : "No paper accounts yet"}>
            {!accounts.loading && (
              <p>
                Open one from the terminal, e.g.{" "}
                <span className="mono">
                  kestrel paper open btc --symbol BTCUSDT --strategy trend-vol-target --research NOTE --venue testnet
                </span>
                , run it with <span className="mono">kestrel paper run btc</span>, and schedule it with{" "}
                <span className="mono">kestrel paper schedule btc</span>.
              </p>
            )}
          </Empty>
        </Panel>
      ) : (
        <>
          <Section title="Accounts">
            <AccountsTable list={list} current={current?.name} onSelect={setChosen} />
          </Section>
          {current && <AccountView name={current.name} />}
        </>
      )}
    </>
  );
}

function AccountsTable({
  list,
  current,
  onSelect,
}: {
  list: PaperSummary[];
  current: string | undefined;
  onSelect: (name: string) => void;
}) {
  return (
    <Panel>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Account</th>
              <th>Market</th>
              <th>Venue</th>
              <th className="num">Equity (USDT)</th>
              <th>Last bar</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {list.map((a) => (
              <tr
                key={a.name}
                className={`clickable${a.name === current ? " selected" : ""}`}
                onClick={() => onSelect(a.name)}
                aria-selected={a.name === current}
              >
                <td>
                  <div style={{ fontWeight: 500 }}>{a.name}</div>
                  <div className="mono small tertiary">{a.strategy}</div>
                </td>
                <td className="nw">
                  {a.symbol} {a.interval}
                </td>
                <td className="nw">{venueLabel(a.venue)}</td>
                <td className="num">{usd(a.equity)}</td>
                <td className="nw secondary">{a.last_bar ? date(a.last_bar) : "not yet"}</td>
                <td className="nw">
                  {a.halted ? <Status tone="bad">Halted</Status> : <Status tone="ok">Running</Status>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function AccountView({ name }: { name: string }) {
  const detail = useResource(() => api.paperAccount(name), [name], 60_000);
  const d = detail.data;
  if (detail.error) {
    return (
      <Panel>
        <Empty title="Couldn't load this account">
          <p>{detail.error}</p>
        </Empty>
      </Panel>
    );
  }
  if (!d || d.name !== name) return null;
  const verdict = trackingVerdict(d);
  const t = d.tracking;
  return (
    <Section
      title={`${d.name} · ${d.strategy}`}
      description={`${d.symbol} ${d.interval} on ${venueLabel(d.venue)}, ${usd(d.initial_cash)} USDT from ${date(d.trade_from)}${
        d.research ? `, under note ${d.research}` : ""
      }.`}
    >
      <div className="run-stack">
        {d.halted && <HaltBanner account={d} />}
        <Panel>
          <div className={`verdict verdict-${verdict.tone}`}>
            <h3>{verdict.tone === "bad" ? "Not tracking the model" : "Tracking the model"}</h3>
            <p className="secondary">{verdict.text}</p>
          </div>
          <div className="stats">
            <Stat label="Equity" value={`${usd(d.equity)}`} note={`Model: ${usd(d.expected.equity.at(-1) ?? null)}`} />
            <Stat label="Holding" value={`${d.position} ${d.symbol.replace("USDT", "")}`} note={`Cash ${usd(d.cash)} USDT`} />
            <Stat label="Days handled" value={t.days} note={t.missed ? `${t.missed} missed` : "None missed"} />
            <Stat
              label="Cost per fill"
              value={t.cost_bps === null ? "n/a" : `${t.cost_bps.toFixed(1)} bps`}
              note={t.expected_bps === null ? "No fills yet" : `${t.expected_bps.toFixed(1)} bps modelled`}
            />
          </div>
        </Panel>
        {d.equity_series.time.length > 1 && (
          <Panel pad>
            <div className="chart-top">
              <span className="small secondary">Equity in USDT (log scale), drawdown underneath</span>
              <span className="cost-key">
                <span>
                  <i className="swatch" style={{ background: "var(--text)" }} />
                  Paper account
                </span>
                <span>
                  <i className="swatch swatch-dashed" />
                  Model (backtest replay)
                </span>
              </span>
            </div>
            <EquityChart equity={d.equity_series} benchmark={d.expected} />
          </Panel>
        )}
        <FillsTable account={d} />
        <DecisionsTable account={d} />
        <SafetyPanel account={d} />
        {d.venue === "testnet" && (
          <p className="small tertiary">
            Testnet fills come from Binance's test order book, not the real market, so their prices and fees aren't
            real costs: the testnet proves the plumbing. Only live fills measure real costs.
          </p>
        )}
      </div>
    </Section>
  );
}

function HaltBanner({ account: d }: { account: PaperDetail }) {
  return (
    <div className="banner" role="alert">
      <strong>Halted:</strong> {d.halted}. It won't trade until resumed from the terminal:{" "}
      <span className="mono">kestrel paper resume {d.name} --reason "..."</span>
    </div>
  );
}

function FillsTable({ account: d }: { account: PaperDetail }) {
  return (
    <Panel>
      {d.fills.length === 0 ? (
        <Empty title="No fills yet" />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Filled</th>
                <th>Side</th>
                <th className="num">Quantity</th>
                <th className="num">Price</th>
                <th className="num">Reference (open)</th>
                <th className="num">Cost</th>
                <th className="num">Model</th>
              </tr>
            </thead>
            <tbody>
              {[...d.fills].reverse().map((f) => (
                <tr key={f.at}>
                  <td className="nw">{dateTime(f.at)}</td>
                  <td className="nw">{f.side}</td>
                  <td className="num">{f.quantity}</td>
                  <td className="num">{usd(f.price)}</td>
                  <td className="num secondary">{usd(f.reference_price)}</td>
                  <td className="num">{f.cost_bps.toFixed(1)} bps</td>
                  <td className="num secondary">{f.expected_bps === null ? "n/a" : `${f.expected_bps.toFixed(1)} bps`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

const DECISIONS_SHOWN = 10;

function DecisionsTable({ account: d }: { account: PaperDetail }) {
  if (d.decisions.length === 0) return null;
  return (
    <Panel>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Decided at the close of</th>
              <th className="num">Target</th>
              <th className="num">Held before</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {d.decisions.slice(0, DECISIONS_SHOWN).map((x) => (
              <tr key={x.bar}>
                <td className="nw">{date(x.bar)}</td>
                <td className="num">{pct(x.target)}</td>
                <td className="num secondary">{pct(x.current)}</td>
                <td className="nw">
                  {x.missed ? (
                    <Status tone="warn">Missed: the run was late</Status>
                  ) : x.band_skip ? (
                    <span className="secondary">Within the 2% band, no trade</span>
                  ) : (
                    <span className="secondary">Traded at the next open, or already there</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {d.decisions.length > DECISIONS_SHOWN && (
        <p className="small tertiary table-foot">
          Latest {DECISIONS_SHOWN} of {d.decisions.length}; every decision is in data/paper/{d.name}/ledger.jsonl.
        </p>
      )}
    </Panel>
  );
}

function SafetyPanel({ account: d }: { account: PaperDetail }) {
  return (
    <Panel pad>
      <h3 className="safety-title">Safety</h3>
      <p className="small secondary">
        Limits in kestrel.toml [risk] halt the account before it trades; only a person resumes it. Kill switch (sells to
        cash, then halts): <span className="mono">kestrel kill {d.name} --reason "..."</span>
      </p>
      {d.events.length > 0 && (
        <ul className="safety-events">
          {d.events.map((e) => (
            <li key={`${e.type}-${e.at}`}>
              <Status tone={e.type === "resume" ? "ok" : "bad"}>{e.type}</Status>{" "}
              <span className="small secondary">{dateTime(e.at)}</span> {e.reason}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
