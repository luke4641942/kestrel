import { useState, type PointerEvent, type ReactNode } from "react";
import { breakEvenLabel, gateKey, gateSetupName, gateTone, hasOwnTarget, spreadBands } from "../summary";
import { api, type CostConfig, type CostCurve, type CostGate, type Quote, type Side } from "../api";
import { Empty, PageHeader, Panel, Section, Status } from "../components/ui";
import { useDebounced, useResource } from "../hooks";

// Every cost figure on this page comes from the API, which runs the backtester's
// CostModel with kestrel.toml. Nothing here re-implements the formula.

/** Example BTC price. Costs scale with notional, so the price only affects rounding. */
const PRICE = 60_000;
/** About A$500 in USDT: the account size Kestrel is built for. */
const HEADLINE_ORDER = 330;
const VOLUMES = [
  { value: 500e6, label: "BTC 4h bar, typical (500m USDT)" },
  { value: 120e6, label: "BTC 4h bar, quiet (120m USDT)" },
  { value: 2e6, label: "Small altcoin 4h bar (2m USDT)" },
];
const FREQUENCIES: [string, number][] = [
  ["Quarterly rebalance", 4],
  ["Monthly", 12],
  ["Weekly", 52],
  ["Twice a week", 104],
  ["Daily", 365],
  ["Every 4h bar", 2190],
];

const PARTS = ["fee", "spread", "impact"] as const;
type Part = (typeof PARTS)[number];
const PART_NAME: Record<Part, string> = { fee: "Fee", spread: "Spread", impact: "Impact" };
const PART_COLOR: Record<Part, string> = {
  fee: "var(--cost-fee)",
  spread: "var(--cost-spread)",
  impact: "var(--cost-impact)",
};

type Breakdown = Record<Part | "total", number>; // in bps of the order

const fmt = (n: number, digits = 2) =>
  n.toLocaleString("en-AU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const bpsDigits = (n: number) => (n < 0.1 ? 3 : n < 10 ? 2 : 1);

function usdt(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toLocaleString("en-AU", { maximumFractionDigits: 2 })}m`;
  if (n >= 1e3) return `${(n / 1e3).toLocaleString("en-AU", { maximumFractionDigits: 1 })}k`;
  return n.toLocaleString("en-AU", { maximumFractionDigits: n < 10 ? 2 : 0 });
}

function breakdown(q: Quote): Breakdown | undefined {
  if (!q.filled || !q.notional) return undefined;
  const per = 1e4 / q.notional;
  return {
    fee: q.fee! * per,
    spread: q.spread_cost! * per,
    impact: q.impact_cost! * per,
    total: q.total_cost! * per,
  };
}

/** Buy then sell the same quantity, as bps of the buy's notional. */
function roundTrip(buy?: Quote, sell?: Quote): Breakdown | undefined {
  if (!buy?.filled || !sell?.filled || !buy.notional) return undefined;
  const per = 1e4 / buy.notional;
  return {
    fee: (buy.fee! + sell.fee!) * per,
    spread: (buy.spread_cost! + sell.spread_cost!) * per,
    impact: (buy.impact_cost! + sell.impact_cost!) * per,
    total: (buy.total_cost! + sell.total_cost!) * per,
  };
}

const bothSides = (notional: number, bar_volume: number) =>
  Promise.all([
    api.quote({ notional, price: PRICE, bar_volume, side: "buy" }),
    api.quote({ notional, price: PRICE, bar_volume, side: "sell" }),
  ]);

export function Costs() {
  const config = useResource(api.costs, []);
  const [side, setSide] = useState<Side>("buy");
  const [logSize, setLogSize] = useState(Math.log10(HEADLINE_ORDER));
  const [volume, setVolume] = useState(VOLUMES[0]!.value);
  const notional = useDebounced(10 ** logSize, 120);

  const headline = useResource(() => bothSides(HEADLINE_ORDER, VOLUMES[0]!.value), []);
  const quotes = useResource(() => bothSides(notional, volume), [notional, volume]);
  const curve = useResource(() => api.curve({ price: PRICE, bar_volume: volume, side }), [side, volume]);
  const gates = useResource(api.costGate, []);

  const header = (
    <PageHeader
      title="What a trade costs"
      subtitle="Every simulated order pays an exchange fee, half the bid-ask spread and market impact. These numbers come straight from the backtester's cost model and kestrel.toml."
    />
  );

  if (config.error) {
    return (
      <>
        {header}
        <Panel>
          <Empty title="The cost model is unavailable">
            <p>
              The API could not load the config: <span className="mono">{config.error}</span>. Fix kestrel.toml and
              this page updates on the next load; no restart needed.
            </p>
          </Empty>
        </Panel>
      </>
    );
  }
  const cfg = config.data;
  const [buy, sell] = quotes.data ?? [];
  const current = side === "buy" ? buy : sell;
  const rt = roundTrip(buy, sell);
  const hero = headline.data ? roundTrip(...headline.data) : undefined;

  return (
    <>
      {header}

      <div className="cost-hero">
        <div>
          <div className="cost-hero-num">
            {hero ? fmt(hero.total, 1) : "–"}
            <small>bps</small>
          </div>
          <p className="small secondary">
            Buy then sell about A$500 of BTC. Every trade has to clear this before it earns anything.
          </p>
        </div>
        {hero && <CostSplit parts={hero} />}
      </div>

      <Section
        title="One order, line by line"
        tour="order"
        description={`Change the order to see where the cost comes from, and when the simulator refuses it. BTC is priced at an example ${fmt(PRICE, 0)} USDT.`}
      >
        <Panel>
          <div className="cost-calc">
            <div className="cost-controls">
              <div className="field">
                <span className="cost-label" id="side-label">
                  Side
                </span>
                <div className="seg" role="group" aria-labelledby="side-label">
                  {(["buy", "sell"] as const).map((s) => (
                    <button key={s} type="button" aria-pressed={side === s} onClick={() => setSide(s)}>
                      {s === "buy" ? "Buy" : "Sell"}
                    </button>
                  ))}
                </div>
              </div>
              <div className="field">
                <label className="cost-label" htmlFor="cost-size">
                  Order size
                </label>
                <input
                  id="cost-size"
                  type="range"
                  min={0}
                  max={7}
                  step={0.01}
                  value={logSize}
                  onChange={(e) => setLogSize(Number(e.target.value))}
                />
                <output className="mono" htmlFor="cost-size">
                  {usdt(10 ** logSize)} USDT
                </output>
                <span className="form-hint">A$500 is about {HEADLINE_ORDER} USDT.</span>
              </div>
              <div className="field">
                <label className="cost-label" htmlFor="cost-volume">
                  Last closed bar's volume
                </label>
                <select id="cost-volume" className="select" value={volume} onChange={(e) => setVolume(Number(e.target.value))}>
                  {VOLUMES.map((v) => (
                    <option key={v.value} value={v.value}>
                      {v.label}
                    </option>
                  ))}
                </select>
                <span className="form-hint">Impact uses the last closed bar, never the bar being filled.</span>
              </div>
            </div>
            <div className="cost-result" aria-live="polite">
              {current && cfg ? <OrderResult quote={current} cfg={cfg} /> : <p className="secondary">Pricing…</p>}
            </div>
          </div>
        </Panel>
      </Section>

      <Section
        title="Cost per order as it grows"
        description="Fee and spread are a flat percentage. Impact grows with the square root of the order's share of the bar's volume, and above the participation limit the simulator rejects the order. At A$500 you sit far to the left, where the fee is almost everything."
      >
        <Panel pad>
          {curve.data ? (
            <SizeChart curve={curve.data} current={current} />
          ) : (
            <p className="secondary">{curve.error ?? "Loading…"}</p>
          )}
        </Panel>
      </Section>

      <Section
        title="What trading often costs per year"
        description="The same round trip, repeated and compounded, at the order size and volume above. This is the return a strategy must make each year before it shows any profit, and why Kestrel trades on 4h and daily bars."
      >
        <Panel pad>
          {rt ? (
            <FrequencyChart roundTripBps={rt.total} />
          ) : (
            <p className="secondary">The order above is rejected, so there is no round trip to repeat.</p>
          )}
        </Panel>
      </Section>

      <Section
        title="Can minute-chart stops pay for themselves?"
        description="The cost gate. Costs are a share of the trade and risk is the distance to the stop, so a trade's cost in R is the round trip divided by the stop's width. These are the stops of the YouTube minute-chart setups (S2's fair value gaps; batch 2's order flow) on real bars before the holdout: widths only, no outcomes, so not a trial. Before costs, a 4R target breaks even at a 20% win rate and 2R at 33%."
      >
        {gates.data && gates.data.length > 0 ? (
          gates.data.map((g) => <GateTable key={gateKey(g)} gate={g} />)
        ) : (
          <Panel>
            <Empty title={gates.loading ? "Loading the cost gate" : "No cost gate run yet"}>
              {!gates.loading && (
                <p>
                  Run <span className="mono">kestrel cost-gate BTCUSDT ETHUSDT SOLUSDT --interval 1m</span>; the result
                  appears here.
                </p>
              )}
            </Empty>
          </Panel>
        )}
      </Section>

      <Section
        title="What was decided, and why"
        description="Decided: a design choice. Assumption: a number chosen without data yet. To verify: must be checked before live trading."
      >
        <Panel>{cfg ? <Decisions cfg={cfg} /> : null}</Panel>
        {cfg && (
          <p className="small tertiary">
            Config fingerprint <span className="mono">{cfg.fingerprint}</span>. Backtest runs will store this, so a
            result can always be traced to the assumptions it used.
          </p>
        )}
      </Section>
    </>
  );
}

function GateTable({ gate }: { gate: CostGate }) {
  const day = (iso: string) => iso.slice(0, 10);
  const bps = (n: number | null) => (n === null ? "–" : fmt(n, 0));
  const own = hasOwnTarget(gate.rows);
  return (
    <Panel>
      <div className="table-wrap">
        <table className="table">
          <caption className="small secondary" style={{ textAlign: "left", padding: "12px 16px 4px" }}>
            {gateSetupName(gate.setup)} · {gate.interval} bars, {day(gate.first)} to {day(gate.last)} ·{" "}
            {gate.round_trip_bps ?? "–"} bps round trip ·
            CARRIES at or below {gate.carries_at}R, FAILS above {gate.fails_above}R
          </caption>
          <thead>
            <tr>
              <th>Market</th>
              <th className="num">Setups</th>
              <th className="num">Median stop, bps</th>
              <th className="num">Middle half, bps</th>
              <th className="num">Cost per trade</th>
              <th className="num">Win rate needed at 2R</th>
              <th className="num">At 4R</th>
              {own && <th className="num">Own target, R</th>}
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {gate.rows.map((r) => (
              <tr key={r.market}>
                <td className="nw" style={{ fontWeight: 500 }}>
                  {r.market}
                </td>
                <td className="num">{r.setups.toLocaleString("en-AU")}</td>
                <td className="num">{bps(r.median_bps)}</td>
                <td className="num nw">
                  {bps(r.p25_bps)} to {bps(r.p75_bps)}
                </td>
                <td className="num">{r.cost_r === null ? "–" : `${fmt(r.cost_r, 2)}R`}</td>
                <td className="num">{breakEvenLabel(r.break_even["2"] ?? null)}</td>
                <td className="num">{breakEvenLabel(r.break_even["4"] ?? null)}</td>
                {own && <td className="num">{r.target_r === null ? "–" : `${fmt(r.target_r, 2)}R`}</td>}
                <td className="nw">
                  <Status tone={gateTone(r.verdict)}>{r.verdict}</Status>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function CostSplit({ parts }: { parts: Breakdown }) {
  return (
    <div className="cost-split">
      <div className="cost-bar" aria-hidden>
        {PARTS.map((k) => (
          <span key={k} style={{ flexGrow: Math.max(parts[k], 0.15), background: PART_COLOR[k] }} />
        ))}
      </div>
      <div className="cost-key">
        {PARTS.map((k) => (
          <span key={k}>
            <i className="swatch" style={{ background: PART_COLOR[k] }} />
            {PART_NAME[k]} <b>{fmt(parts[k], bpsDigits(parts[k]))} bps</b>
          </span>
        ))}
      </div>
    </div>
  );
}

function OrderResult({ quote, cfg }: { quote: Quote; cfg: CostConfig }) {
  if (!quote.filled) {
    return (
      <>
        <div className="cost-reject">
          <strong>Rejected</strong>
          <span className="mono">{quote.reason}</span>
        </div>
        <p className="small secondary">
          The backtester records rejections like this instead of quietly filling the order. A strategy that keeps
          hitting them is too small or too large for its market.
        </p>
      </>
    );
  }
  const b = breakdown(quote)!;
  const c = cfg.costs;
  const rows: [ReactNode, string][] = [
    ["Quantity, rounded down to the exchange step", `${fmt(quote.quantity!, 5)} BTC`],
    ["Reference price (next bar's open)", fmt(quote.reference_price)],
    ["Fill price after spread and impact", fmt(quote.price!)],
    [`Fee (${c.taker_fee_bps} bps of executed value)`, fmt(quote.fee!, 4)],
    [`Spread (${c.half_spread_bps} bp)`, fmt(quote.spread_cost!, 4)],
    [`Impact (${c.impact_coef_bps} bps × √participation)`, fmt(quote.impact_cost!, 4)],
  ];
  return (
    <>
      <div className="cost-result-head">
        <div>
          <div className="small secondary">Total cost</div>
          <div className="cost-total">{fmt(quote.total_cost!, quote.total_cost! < 1 ? 4 : 2)} USDT</div>
        </div>
        <div className="small secondary cost-result-meta">
          {fmt(b.total, 2)} bps of the order
          <br />
          {fmt(quote.participation! * 100, quote.participation! < 1e-4 ? 5 : 3)}% of the bar's volume
        </div>
      </div>
      <CostSplit parts={b} />
      <dl className="dl">
        {rows.map(([label, value], i) => (
          <LedgerRow key={i} label={label} value={value} />
        ))}
        <dt className="cost-strong">Cash {quote.side === "buy" ? "paid" : "received"}</dt>
        <dd className="cost-strong">{fmt(Math.abs(quote.cash_flow!), 4)} USDT</dd>
      </dl>
    </>
  );
}

function LedgerRow({ label, value }: { label: ReactNode; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className="mono">{value}</dd>
    </>
  );
}

// ---------- charts ----------

interface Tip {
  left: number;
  top: number;
  content: ReactNode;
}

function useTip() {
  const [tip, setTip] = useState<Tip>();
  const place = (e: PointerEvent, content: ReactNode) => {
    const box = (e.currentTarget as Element).closest(".chart-box")!.getBoundingClientRect();
    setTip({ left: Math.min(e.clientX - box.left + 14, box.width - 200), top: e.clientY - box.top + 14, content });
  };
  const node = tip ? (
    <div className="chart-tip" style={{ left: tip.left, top: tip.top }}>
      {tip.content}
    </div>
  ) : null;
  return { place, clear: () => setTip(undefined), node };
}

function SizeChart({ curve, current }: { curve: CostCurve; current?: Quote }) {
  const W = 1120, H = 316, L = 40, R = 150, T = 14, B = 46;
  const X0 = 0, X1 = 7;
  const yMax = Math.max(25, Math.ceil(Math.max(...curve.points.map((p) => p.total_bps)) / 5) * 5);
  const x = (n: number) => L + ((Math.log10(n) - X0) / (X1 - X0)) * (W - L - R);
  const y = (b: number) => T + (1 - b / yMax) * (H - T - B);
  const pts = curve.points.filter((p) => p.notional <= 10 ** X1);
  const last = pts.at(-1);
  const { place, clear, node } = useTip();
  const [hairX, setHairX] = useState<number>();

  const layer = (lo: (p: (typeof pts)[0]) => number, hi: (p: (typeof pts)[0]) => number) =>
    pts
      .map((p) => `${x(p.notional).toFixed(1)},${y(hi(p)).toFixed(1)}`)
      .concat(pts.slice().reverse().map((p) => `${x(p.notional).toFixed(1)},${y(lo(p)).toFixed(1)}`))
      .join(" ");
  const line = (f: (p: (typeof pts)[0]) => number) =>
    pts.map((p) => `${x(p.notional).toFixed(1)},${y(f(p)).toFixed(1)}`).join(" ");

  const onMove = (e: PointerEvent<SVGRectElement>) => {
    const svg = e.currentTarget.ownerSVGElement!;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const sx = pt.matrixTransform(svg.getScreenCTM()!.inverse()).x;
    setHairX(sx);
    const n = 10 ** (X0 + ((sx - L) / (W - L - R)) * (X1 - X0));
    if (n < curve.min_notional) {
      place(e, <span>{usdt(n)} USDT: below Binance's {curve.min_notional} USDT minimum</span>);
      return;
    }
    if (n > curve.max_notional) {
      place(e, <span>{usdt(n)} USDT: rejected, over the participation limit</span>);
      return;
    }
    const p = pts.reduce((a, b) =>
      Math.abs(Math.log10(b.notional / n)) < Math.abs(Math.log10(a.notional / n)) ? b : a,
    );
    place(
      e,
      <>
        <div className="chart-tip-head">{usdt(p.notional)} USDT order</div>
        {PARTS.map((k) => (
          <div className="chart-tip-row" key={k}>
            <span>
              <i className="swatch" style={{ background: PART_COLOR[k] }} />
              {PART_NAME[k]}
            </span>
            <span className="mono">{fmt(p[`${k}_bps`], 2)}</span>
          </div>
        ))}
        <div className="chart-tip-row chart-tip-total">
          <span>Total, bps</span>
          <span className="mono">{fmt(p.total_bps, 2)}</span>
        </div>
      </>,
    );
  };

  const cur = current?.filled ? breakdown(current) : undefined;
  const regions: [number, number, string[], boolean][] = [
    [1, curve.min_notional, ["Below Binance's", `${curve.min_notional} USDT minimum`], true],
    [curve.max_notional, 10 ** X1, ["Rejected: over", "participation limit"], false],
  ];

  return (
    <div className="chart-box">
      <div className="chart-top">
        <span className="small secondary">
          One {curve.side}, in bps of the order, against {usdt(curve.bar_volume)} USDT of bar volume
        </span>
        <span className="cost-key">
          {PARTS.map((k) => (
            <span key={k}>
              <i className="swatch" style={{ background: PART_COLOR[k] }} />
              {PART_NAME[k]}
            </span>
          ))}
        </span>
      </div>
      <div className="chart-scroll">
        <svg className="cost-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Stacked cost in basis points against order size">
          {Array.from({ length: yMax / 5 + 1 }, (_, i) => i * 5).map((b) => (
            <g key={b}>
              <line x1={L} x2={W - R} y1={y(b)} y2={y(b)} stroke="var(--line)" />
              <text x={L - 8} y={y(b) + 4} textAnchor="end">
                {b}
              </text>
            </g>
          ))}
          {Array.from({ length: X1 + 1 }, (_, p) => 10 ** p).map((n) => (
            <text key={n} x={x(n)} y={H - B + 16} textAnchor="middle">
              {usdt(n)}
            </text>
          ))}
          <text x={L} y={H - 4} className="lab2">
            Order size, USDT (log scale)
          </text>
          {regions.map(([a, b, label, top]) => {
            const xa = x(Math.max(a, 1)), xb = x(Math.min(b, 10 ** X1));
            if (xb <= xa) return null;
            const ty = top ? T + 14 : H - B - 8 - 15 * (label.length - 1);
            return (
              <g key={label[0]}>
                <rect x={xa} y={T} width={xb - xa} height={H - T - B} fill="var(--cost-hatch)" />
                <text x={xa + 6} y={ty} className="lab2">
                  {label.map((l, i) => (
                    <tspan key={l} x={xa + 6} dy={i ? 15 : 0}>
                      {l}
                    </tspan>
                  ))}
                </text>
              </g>
            );
          })}
          {pts.length > 1 && (
            <>
              <polygon points={layer(() => 0, (p) => p.fee_bps)} fill={PART_COLOR.fee} fillOpacity={0.85} />
              <polygon points={layer((p) => p.fee_bps, (p) => p.fee_bps + p.spread_bps)} fill={PART_COLOR.spread} fillOpacity={0.85} />
              <polygon points={layer((p) => p.fee_bps + p.spread_bps, (p) => p.total_bps)} fill={PART_COLOR.impact} fillOpacity={0.85} />
              <polyline points={line((p) => p.fee_bps)} fill="none" stroke="var(--bg)" strokeWidth={1.5} />
              <polyline points={line((p) => p.fee_bps + p.spread_bps)} fill="none" stroke="var(--bg)" strokeWidth={1.5} />
              <polyline points={line((p) => p.total_bps)} fill="none" stroke="var(--text)" strokeWidth={1.5} />
            </>
          )}
          {last && (
            <g className="lab">
              <text x={W - R + 10} y={y(last.fee_bps / 2) + 4}>
                Fee {fmt(last.fee_bps, 1)}
              </text>
              <text x={W - R + 10} y={y(last.fee_bps + last.spread_bps) - 5}>
                Spread {fmt(last.spread_bps, 0)}
              </text>
              <text x={W - R + 10} y={Math.min(y(last.total_bps) + 18, y(last.fee_bps + last.spread_bps) - 24)}>
                Impact {fmt(last.impact_bps, 1)}
              </text>
              <text x={W - R + 10} y={y(last.total_bps) - 6} className="lab2">
                Total {fmt(last.total_bps, 1)} bps
              </text>
            </g>
          )}
          {cur && current?.notional && (
            <g>
              <line x1={x(current.notional)} x2={x(current.notional)} y1={T} y2={H - B} stroke="var(--text)" strokeDasharray="3 3" />
              <circle cx={x(current.notional)} cy={y(cur.total)} r={4.5} fill="var(--text)" stroke="var(--bg)" strokeWidth={2} />
              <text x={x(current.notional) + 8} y={y(cur.total) - 8} className="lab">
                Your order: {fmt(cur.total, 1)} bps
              </text>
            </g>
          )}
          {hairX !== undefined && <line x1={hairX} x2={hairX} y1={T} y2={H - B} stroke="var(--text-3)" />}
          <rect
            x={L}
            y={T}
            width={W - L - R}
            height={H - T - B}
            fill="transparent"
            onPointerMove={onMove}
            onPointerLeave={() => {
              clear();
              setHairX(undefined);
            }}
          />
        </svg>
      </div>
      {node}
    </div>
  );
}

function FrequencyChart({ roundTripBps }: { roundTripBps: number }) {
  const rows = FREQUENCIES.map(([label, n]) => ({ label, n, drag: 1 - (1 - roundTripBps / 1e4) ** n }));
  const W = 1120, L = 150, R = 70, T = 8, rowH = 36, barH = 14;
  const H = T + rows.length * rowH + 22;
  const x = (v: number) => L + v * (W - L - R);
  const { place, clear, node } = useTip();
  return (
    <div className="chart-box">
      <div className="chart-scroll">
        <svg className="cost-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Yearly cost of trading at each frequency">
          {[0, 0.25, 0.5, 0.75, 1].map((g) => (
            <g key={g}>
              <line x1={x(g)} x2={x(g)} y1={T} y2={H - 22} stroke="var(--line)" />
              <text x={x(g)} y={H - 6} textAnchor="middle">
                {g * 100}%
              </text>
            </g>
          ))}
          {rows.map((r, i) => {
            const cy = T + i * rowH + rowH / 2;
            const w = Math.max(x(r.drag) - L, 2);
            const run = Math.max(w - 4, 0);
            return (
              <g key={r.label}>
                <text x={L - 12} y={cy - 2} textAnchor="end" className="lab">
                  {r.label}
                </text>
                <text x={L - 12} y={cy + 12} textAnchor="end">
                  {r.n.toLocaleString("en-AU")} round trips
                </text>
                {/* 4px rounded data end, square at the baseline. */}
                <path
                  d={`M${L},${cy - barH / 2} h${run} a4,4 0 0 1 4,4 v${barH - 8} a4,4 0 0 1 -4,4 h${-run} Z`}
                  fill="var(--cost-fee)"
                />
                <text x={L + w + 8} y={cy + 4} className="lab">
                  {fmt(r.drag * 100, r.drag < 0.1 ? 1 : 0)}%
                </text>
                <rect
                  x={0}
                  y={cy - rowH / 2}
                  width={W}
                  height={rowH}
                  fill="transparent"
                  onPointerMove={(e) =>
                    place(
                      e,
                      <>
                        <div className="chart-tip-head">{r.label}</div>
                        <div className="chart-tip-row">
                          <span>Round trips a year</span>
                          <span className="mono">{r.n.toLocaleString("en-AU")}</span>
                        </div>
                        <div className="chart-tip-row">
                          <span>Cost each</span>
                          <span className="mono">{fmt(roundTripBps, 1)} bps</span>
                        </div>
                        <div className="chart-tip-row">
                          <span>Lost per year</span>
                          <span className="mono">{fmt(r.drag * 100, 1)}%</span>
                        </div>
                      </>,
                    )
                  }
                  onPointerLeave={clear}
                />
              </g>
            );
          })}
        </svg>
      </div>
      {node}
    </div>
  );
}

// ---------- decisions ----------


type Kind = "decided" | "assumption" | "verify";
const KIND_LABEL: Record<Kind, string> = { decided: "Decided", assumption: "Assumption", verify: "To verify" };

function Decisions({ cfg }: { cfg: CostConfig }) {
  const c = cfg.costs;
  const minNotional = cfg.instruments.find((i) => i.symbol === "BTCUSDT")?.min_notional ?? 5;
  const items: [Kind, string, string][] = [
    ["decided", "Costs are in basis points everywhere", "One unit for fee, spread and impact. Mixing fractions and bps in one file is how factor-of-100 bugs happen."],
    ["decided", `Fee: ${c.taker_fee_bps} bps per side`, "Binance's base tier (10 bps) with the BNB discount, 25% off. Caveat: it needs fees switched to BNB and a small BNB balance kept in the account; without it Binance charges the full 10 bps. Runs before 2026-11-15 used 10."],
    ["assumption", `Spread: ${c.half_spread_bps} bp per side`, "There is no order-book history yet. BTC usually quotes far tighter, so this errs on the expensive side."],
    [
      "assumption",
      `Other coins' spreads: ${spreadBands(cfg.spread_tiers)}`,
      "For strategies that rank many coins. A coin's band is set by its median daily volume over the last 30 days, known at the time. An estimate from daily highs and lows was tried and dropped: it gave BTC 21 bps against a real spread near 1.",
    ],
    ["assumption", `Impact: ${c.impact_coef_bps} bps × √(order ÷ bar volume)`, "The square-root law is the standard market-impact model. At A$500 it is almost zero; it matters for big orders and thin markets."],
    ["decided", "Impact uses the last closed bar's volume", "An order filled at the next bar's open can't know that bar's volume yet. Using it would be lookahead."],
    ["decided", `Orders over ${fmt(c.max_participation * 100, 0)}% of a bar's volume are rejected`, "Rather than fill at a price the market could not have offered. Rejections are recorded with the reason."],
    ["decided", "Quantities round down to the step size", "Binance trades BTC in steps of 0.00001. Rounding up could spend cash the account doesn't have."],
    ["decided", "Exchange minimums enforced", `Orders under ${minNotional} USDT are refused, as on Binance. At A$500 split across strategies, this is a real limit.`],
    ["decided", "Money uses exact decimals, not floats", "0.1 + 0.2 isn't 0.3 in floating point. Fills, fees and cash are Decimal end to end; this page only rounds for display."],
    ["decided", "One formula for both engines", "The event-driven engine fills orders; the vectorised engine applies a cost rate. Both use one formula and a test checks they agree."],
    ["decided", "Config is kestrel.toml, not a settings page", "No defaults in code, unknown keys are errors, and each run will store the config's fingerprint so old results keep their meaning."],
    ["decided", `Accounting is in ${cfg.account_currency}`, "Binance spot quotes in USDT. The dashboard converts to A$ for display only."],
    ["assumption", `Fees are charged in ${cfg.account_currency}`, "Binance takes a buyer's fee in the coin bought. Charging the same value in USDT keeps one ledger and costs the same."],
    ["verify", "Binance order rules", "Step sizes and minimums for BTC and ETH were not checked against the live exchange API. P4 will load them from it."],
  ];
  return (
    <ul className="decisions">
      {items.map(([kind, title, body]) => (
        <li key={title}>
          <span className={`chip ${kind}`}>{KIND_LABEL[kind]}</span>
          <div>
            <h3>{title}</h3>
            <p>{body}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}
