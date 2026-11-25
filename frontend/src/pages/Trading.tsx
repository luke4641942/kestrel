import { useState } from "react";
import { api, type PaperDetail, type PaperSummary, type Waiting } from "../api";
import { EquityChart } from "../components/EquityChart";
import { Empty, PageHeader, Panel, Section, Stat, Status } from "../components/ui";
import { date, dateTime } from "../format";
import { useResource } from "../hooks";
import {
  accountProfit,
  conditionText,
  holdingView,
  ruleText,
  waitingSummary,
  isBasket,
  targetsText,
  totalView,
  tradeReturn,
  tradesSummary,
  trackingVerdict,
  decayView,
  venueLabel,
  versusHolding,
} from "../paperView";
import { money } from "../summary";

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
          <TotalView />
          <Section title="Accounts" tour="accounts">
            <AccountsTable list={list} current={current?.name} onSelect={setChosen} />
          </Section>
          {current && <AccountView name={current.name} />}
        </>
      )}
    </>
  );
}

const signedPct = (x: number, digits = 2) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(digits)}%`;

/** Every account added up: the whole paper book's equity and net profit, against the
 * model's accounts added up. */
function TotalView() {
  const total = useResource(api.paperTotal, [], 60_000);
  const t = total.data;
  if (!t || t.accounts === 0) return null;
  const v = totalView(t);
  return (
    <Section
      title="All accounts"
      description={`Every paper account added up: ${t.accounts} accounts, ${usd(t.initial_cash)} USDT of starting cash. An account counts its starting cash until its first mark.`}
    >
      <div className="run-stack">
        <Panel>
          <div className="stats">
            <Stat
              label="Net profit"
              value={v ? `${money(v.profit)} USDT` : "n/a"}
              note={v ? `${signedPct(v.share)} on ${usd(t.initial_cash)} USDT` : "No marks yet"}
            />
            <Stat label="Equity" value={`${usd(t.equity)} USDT`} note={`Model: ${usd(t.expected_equity)} USDT`} />
            <Stat
              label="Against the model"
              value={v?.model === null || v?.model === undefined ? "n/a" : signedPct(v.model)}
              note="Fills, missed days and fees, added up"
            />
          </div>
        </Panel>
        {t.equity_series.time.length > 1 ? (
          <Panel pad>
            <div className="chart-top">
              <span className="small secondary">All accounts' equity in USDT (log scale), drawdown underneath</span>
              <span className="cost-key">
                <span>
                  <i className="swatch" style={{ background: "var(--text)" }} />
                  Paper accounts
                </span>
                <span>
                  <i className="swatch swatch-dashed" />
                  Model (backtest replay)
                </span>
              </span>
            </div>
            <EquityChart equity={t.equity_series} benchmark={t.expected} />
          </Panel>
        ) : (
          <p className="small tertiary">The chart starts once the accounts have two marks.</p>
        )}
      </div>
    </Section>
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
              <th className="num">Profit</th>
              <th className="num" title="Holding its market over the same days, one entry's costs paid (a basket: its coins in equal parts)">Holding</th>
              <th className="num" title="The account's return less holding's, in percentage points. Weeks of this are noise; the scorecard compares against holding at the same exposure">vs holding</th>
              <th className="num">Trades</th>
              <th>Waiting for</th>
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
                  {isBasket(a) ? "Basket" : a.symbol} {a.interval}
                </td>
                <td className="nw">{venueLabel(a.venue)}</td>
                <td className="num">{usd(a.equity)}</td>
                <td className="num">{accountProfit(a) ? money(accountProfit(a)!.profit) : "n/a"}</td>
                <HoldingCells a={a} />
                <td className="num">{a.trade_count}</td>
                <td className="small secondary">{waitingSummary(a.waiting)}</td>
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

function HoldingCells({ a }: { a: PaperSummary }) {
  const v = versusHolding(a);
  if (v === null) {
    return (
      <>
        <td className="num secondary">n/a</td>
        <td className="num secondary">n/a</td>
      </>
    );
  }
  const pts = `${signedPct(v.gap, 1).slice(0, -1)} pts`;
  return (
    <>
      <td className="num secondary">{signedPct(v.hold, 1)}</td>
      <td className="num">{pts}</td>
    </>
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
  const holding = holdingView(d);
  const p = accountProfit(d);
  const trades = tradesSummary(d.trades);
  const fade = decayView(d.decay);
  const model = d.expected.equity.at(-1) ?? null;
  const bps = (x: number | null) => (x === null ? "n/a" : `${x.toFixed(1)} bps`);
  return (
    <Section
      title={`${d.name} · ${d.strategy}`}
      description={`${isBasket(d) ? "A basket of coins" : d.symbol} ${d.interval} on ${venueLabel(d.venue)}, ${usd(d.initial_cash)} USDT from ${date(d.trade_from)}${
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
            <Stat
              label="Profit since it opened"
              value={p ? `${money(p.profit)} USDT` : "n/a"}
              note={p ? `${signedPct(p.share)}; equity ${usd(d.equity)}, model ${usd(model)}` : "No marks yet"}
            />
            <Stat label="Trades" value={d.trades.length} note={trades.text} />
            <Stat label="Holding now" value={holding.value} note={holding.note} />
            <Stat
              label="Days handled"
              value={t.days}
              note={`${t.missed ? `${t.missed} missed` : "None missed"}; fills cost ${bps(t.cost_bps)} vs ${bps(t.expected_bps)} modelled`}
            />
            <Stat
              label="Decay check"
              value={fade.tone ? <Status tone={fade.tone}>{fade.value}</Status> : fade.value}
              note={fade.note}
            />
          </div>
        </Panel>
        {d.waiting && <WaitingPanel waiting={d.waiting} />}
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
        <TradesTable account={d} />
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

function WaitingPanel({ waiting: w }: { waiting: Waiting }) {
  const price = (x: number) => x.toLocaleString("en-AU", { maximumSignificantDigits: 6 });
  return (
    <Panel>
      <h3 className="table-title">What it's waiting for</h3>
      <p className="table-note small secondary">
        {ruleText(w)} As of the {date(w.bar)} close.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Test</th>
              <th className="num">Close</th>
              <th className="num">Must beat</th>
              <th>Now</th>
              <th>Next close</th>
            </tr>
          </thead>
          <tbody>
            {w.conditions.map((c) => (
              <tr key={c.label}>
                <td>{c.label}</td>
                <td className="num">{price(c.value)}</td>
                <td className="num">{price(c.level)}</td>
                <td className="nw">{c.met ? <Status tone="ok">Holds</Status> : <Status tone="warn">Not yet</Status>}</td>
                <td className="small secondary">{conditionText(c)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
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

function TradesTable({ account: d }: { account: PaperDetail }) {
  return (
    <Panel>
      <h3 className="table-title">Trades</h3>
      <p className="small tertiary table-note">
        A trade runs from buying a coin to holding none of it again; resizes in between belong to it. Profit is after fees.
      </p>
      {d.trades.length === 0 ? (
        <Empty title="No trades yet" />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Coin</th>
                <th>Opened</th>
                <th>Closed</th>
                <th className="num">Bought for</th>
                <th className="num">Sold for</th>
                <th className="num">Profit (USDT)</th>
                <th className="num">Return</th>
                <th className="num">Fills</th>
              </tr>
            </thead>
            <tbody>
              {[...d.trades].reverse().map((x) => {
                const r = tradeReturn(x);
                return (
                  <tr key={`${x.symbol}-${x.opened}`}>
                    <td className="nw">{x.symbol.replace(/USDT$/, "")}</td>
                    <td className="nw">{date(x.opened)}</td>
                    <td className="nw">{x.closed ? date(x.closed) : <Status tone="neutral">Open</Status>}</td>
                    <td className="num">{usd(x.bought)}</td>
                    <td className="num">
                      {x.closed ? usd(x.sold) : x.value === null ? "n/a" : `${usd(x.sold + x.value)} now`}
                    </td>
                    <td className="num">{x.profit === null ? "n/a" : money(x.profit)}</td>
                    <td className="num">{r === null ? "n/a" : signedPct(r)}</td>
                    <td className="num secondary">{x.fills}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function FillsTable({ account: d }: { account: PaperDetail }) {
  return (
    <Panel>
      <h3 className="table-title">Fills</h3>
      <p className="small tertiary table-note">
        Every order. Cost is what the fill paid against the bar's open plus fees, in basis points (100 bps = 1%); the
        model column is the backtest's estimate for the same order.
      </p>
      {d.fills.length === 0 ? (
        <Empty title="No fills yet" />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Filled</th>
                {isBasket(d) && <th>Coin</th>}
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
                <tr key={`${f.at}-${f.symbol}`}>
                  <td className="nw">{dateTime(f.at)}</td>
                  {isBasket(d) && <td className="nw">{f.symbol.replace(/USDT$/, "")}</td>}
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
      <h3 className="table-title">Decisions</h3>
      <p className="small tertiary table-note">
        What the strategy wanted at each daily close, as a share of the account, and what it held before.
      </p>
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
                <td className="num">{x.targets ? targetsText(x.targets) : pct(x.target)}</td>
                <td className="num secondary">{x.current === null ? "n/a" : pct(x.current)}</td>
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
