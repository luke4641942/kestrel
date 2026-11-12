// Typed client for the Kestrel API. Types mirror src/kestrel/api/models.py
// (the OpenAPI schema at /api/openapi.json is the source of truth).

export type Interval = "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d";
export const INTERVALS: Interval[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];

export interface Health {
  status: string;
  version: string;
  data_dir: string;
}

export interface Dataset {
  venue: string;
  symbol: string;
  interval: Interval;
  months: number;
  rows: number;
  first: string | null;
  last: string | null;
}

export interface Bars {
  symbol: string;
  interval: Interval;
  time: number[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  volume: number[];
}

export interface Gap {
  after: string;
  before: string;
  missing_bars: number;
}

export interface Quality {
  symbol: string;
  interval: Interval;
  is_clean: boolean;
  rows: number;
  first: string | null;
  last: string | null;
  duplicates: number;
  unsorted: boolean;
  misaligned: number;
  ohlc_violations: number;
  non_positive_prices: number;
  negative_volumes: number;
  nulls: number;
  missing_bars: number;
  gaps: Gap[];
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Job {
  id: string;
  kind: string;
  params: Record<string, unknown>;
  status: JobStatus;
  progress: number;
  message: string;
  logs: string[];
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type Side = "buy" | "sell";

export interface CostConfig {
  account_currency: string;
  fingerprint: string;
  costs: {
    taker_fee_bps: number;
    half_spread_bps: number;
    impact_coef_bps: number;
    max_participation: number;
  };
  backtest: { no_trade_band: number; holdout_start: string };
  instruments: { symbol: string; step_size: number; min_qty: number; min_notional: number }[];
  /** Half-spread bands for the coin universe: [minimum median daily USDT volume, bps]. */
  spread_tiers: [number, number][];
}

/** One simulated market order. Cost fields are null when `filled` is false. */
export interface Quote {
  symbol: string;
  side: Side;
  filled: boolean;
  reason: string | null;
  reference_price: number;
  bar_volume: number;
  quantity: number | null;
  price: number | null;
  notional: number | null;
  participation: number | null;
  fee: number | null;
  spread_cost: number | null;
  impact_cost: number | null;
  total_cost: number | null;
  cash_flow: number | null;
}

export interface CostCurve {
  symbol: string;
  side: Side;
  reference_price: number;
  bar_volume: number;
  min_notional: number;
  max_notional: number;
  points: { notional: number; fee_bps: number; spread_bps: number; impact_bps: number; total_bps: number }[];
}

export interface QuoteParams {
  notional: number;
  price: number;
  bar_volume: number;
  side: Side;
  symbol?: string;
}

export interface ParamInfo {
  name: string;
  default: number;
  low: number;
  high: number;
  description: string;
}

export type StrategyKind = "benchmark" | "engine-test" | "research" | "control";

export interface StrategyInfo {
  key: string;
  kind: StrategyKind;
  description: string;
  params: ParamInfo[];
  /** Ranks the whole coin universe instead of trading one market. */
  universe: boolean;
}

export interface StrategySource {
  key: string;
  source: string;
  commit: string | null;
  dirty: boolean;
}

export interface SignalsRequest {
  symbol: string;
  interval: Interval;
  start: string;
  end: string;
  params: Record<string, number>;
}

export interface Signals {
  /** "entries": weight is 1 on bars that signal an entry (bracket strategies). */
  kind: "weights" | "entries";
  /** Bars in the requested range; signals use all of them, only the latest are sent. */
  total_bars: number;
  time: number[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  weight: number[];
}

export type ResearchStatus = "idea" | "specified" | "backtested" | "validated" | "paper" | "live" | "rejected";

export interface ResearchSummary {
  slug: string;
  name: string;
  status: ResearchStatus;
  created_at: string;
  version: number;
  frozen: boolean;
  implementation: string | null;
  runs: number;
  /** Distinct configurations tested; identical re-runs count once. */
  trials: number;
  /** The best deflated Sharpe among this hypothesis's trials. */
  deflated_sharpe?: number | null;
}

/** What to do with a tested strategy (research/decisions.toml). */
export interface Decision {
  strategy: string;
  name: string;
  market: string;
  decision: "pursue" | "watch" | "drop";
  decided: string;
  reason: string;
  evidence: string[];
}

/** The trial log as a whole: what every result is corrected for (P3). */
export interface Validation {
  /** Distinct configurations tried before the holdout, research or not. */
  trials: number;
  sharpe_std: number;
  /** Annual Sharpe the best of that many worthless strategies would reach by luck. */
  expected_max_sharpe: number;
  /** Robustness checks and walk-forwards, newest first; their settings count as trials. */
  studies: Study[];
}

export interface Study {
  id: string;
  created_at: string;
  kind: "robustness" | "walk-forward";
  strategy: string;
  symbol: string;
  interval: Interval;
  start: string;
  end: string;
  looks: { params: Record<string, number>; sharpe: number | null }[];
  /** ROBUST, FRAGILE or NO EDGE (robustness); SURVIVES or KILL (walk-forward). */
  verdict: string;
}

export interface Note {
  version: number;
  created_at: string;
  sections: Record<string, string>;
  implementation: string | null;
  frozen_at: string | null;
  change_reason: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  at: string;
  note_version: number | null;
}

export interface ResearchDetail extends ResearchSummary {
  /** Section key -> heading, in display order. */
  sections: Record<string, string>;
  notes: Note[];
  history: { status: ResearchStatus; at: string; reason: string }[];
  trial_log: RunSummary[];
  chat: ChatMessage[];
  assistant_available: boolean;
  assistant_hint: string | null;
}

export interface BacktestRequest {
  symbol: string;
  interval: Interval;
  strategy: string;
  start: string;
  end: string;
  initial_cash: number;
  params?: Record<string, number>;
  /** Research strategy slug: runs its frozen note's implementation as a logged trial. */
  research?: string;
}

/** Backtest metrics. null where a value is undefined (e.g. Sharpe of flat equity). */
export interface Metrics {
  start: string;
  end: string;
  days: number;
  final_equity: number;
  total_return: number;
  cagr: number | null;
  volatility: number | null;
  sharpe: number | null;
  sharpe_ci: (number | null)[];
  psr: number | null;
  sortino: number | null;
  max_drawdown: number;
  longest_underwater_days: number;
  skew: number | null;
  kurtosis: number | null;
  exposure: number;
  trades: number;
  rejections: number;
  band_skips: number;
  fees: number;
  spread_costs: number;
  impact_costs: number;
  total_costs: number;
  turnover: number | null;
  few_trades: boolean;
  /** Bracket strategies only; null otherwise. */
  round_trips?: number | null;
  win_rate?: number | null;
  avg_win_r?: number | null;
  avg_loss_r?: number | null;
  expectancy_r?: number | null;
  profit_factor?: number | null;
  avg_cost_r?: number | null;
  risk_capped?: number | null;
}

export interface RunSummary {
  id: string;
  created_at: string;
  strategy: string;
  strategy_kind: StrategyKind;
  style: "weights" | "brackets";
  params: Record<string, number>;
  research: { slug: string; version: number } | null;
  strategy_name: string;
  /** The market, or UNIVERSE for a cross-sectional run. */
  symbol: string;
  /** What the benchmark held; older runs leave it out (it was the market itself). */
  benchmark_symbol?: string | null;
  /** Coins ranked, for a cross-sectional run. */
  universe_coins?: number | null;
  forced_exits?: number;
  /** P(true Sharpe beats the best of every strategy tried by luck); null for the benchmark. */
  deflated_sharpe?: number | null;
  deflated_trials?: number | null;
  interval: Interval;
  start: string;
  end: string;
  initial_cash: string;
  account_currency: string;
  used_holdout: boolean;
  config_fingerprint: string;
  code: { commit: string | null; dirty: boolean } | null;
  metrics: Metrics;
  benchmark_metrics: Metrics;
}

export interface EquitySeries {
  time: number[];
  equity: number[];
  drawdown: number[];
}

export interface Analysis {
  return_ci: (number | null)[];
  p_profit: number;
  expectancy_ci: (number | null)[] | null;
  p_positive_expectancy: number | null;
  min_track_record_days: number | null;
  gross_profit: number;
  cost_per_round_trip_bps: number | null;
  break_even_cost_bps: number | null;
  beta: number | null;
  correlation: number | null;
  matched_btc_return: number | null;
  worst_day: number;
  worst_month: number;
  expected_shortfall: number;
  years: { year: number; strategy: number | null; btc: number | null }[];
  payoff_ratio: number | null;
  profit_without_top5: number | null;
  holding_buckets: Record<string, number>;
  long_term_gain_share: number | null;
  open_at_end: boolean;
  resamples: number;
  block_days: number;
}

export interface RunDetail extends RunSummary {
  config: Record<string, unknown>;
  equity: EquitySeries;
  benchmark: EquitySeries;
  fills: {
    time: string;
    side: Side;
    quantity: number;
    reference_price: number;
    price: number;
    fee: number;
    spread_cost: number;
    impact_cost: number;
    cash_flow: number;
  }[];
  rejections: { time: string; side: Side; quantity: number; reason: string }[];
  trades: {
    entry_time: string;
    exit_time: string;
    exit_reason: "target" | "stop" | "signal" | "end of data";
    quantity: number;
    entry_price: number;
    exit_price: number;
    stop: number;
    /** null when the strategy set no target. */
  target: number | null;
    risk: number;
    pnl: number;
    costs: number;
    r_multiple: number;
    cost_r: number;
    risk_capped: boolean;
  }[];
  /** Missing for runs saved before the analysis existed. */
  analysis: Analysis | null;
}

export interface IngestRequest {
  symbol: string;
  interval: Interval;
  start: string;
  end?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail =
        typeof body.detail === "string"
          ? body.detail
          : Array.isArray(body.detail)
            ? body.detail
                .map((d: { loc: string[]; msg: string }) => `${d.loc.at(-1)}: ${d.msg.replace(/^Value error, /, "")}`)
                .join("; ")
            : detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;
const query = (p: object) =>
  new URLSearchParams(Object.entries(p).map(([k, v]) => [k, String(v)])).toString();

export const api = {
  health: () => request<Health>("/health"),
  datasets: () => request<Dataset[]>("/datasets"),
  bars: (symbol: string, interval: Interval, limit = 2000) =>
    request<Bars>(`/datasets/${enc(symbol)}/${interval}/bars?limit=${limit}`),
  quality: (symbol: string, interval: Interval) =>
    request<Quality>(`/datasets/${enc(symbol)}/${interval}/quality`),
  jobs: () => request<Job[]>("/jobs"),
  ingest: (body: IngestRequest) =>
    request<Job>("/jobs/ingest", { method: "POST", body: JSON.stringify(body) }),
  cancelJob: (id: string) => request<Job>(`/jobs/${enc(id)}/cancel`, { method: "POST" }),
  strategies: () => request<StrategyInfo[]>("/strategies"),
  startBacktest: (body: BacktestRequest) =>
    request<Job>("/jobs/backtest", { method: "POST", body: JSON.stringify(body) }),
  backtests: () => request<RunSummary[]>("/backtests"),
  validation: () => request<Validation>("/validation"),
  decisions: () => request<Decision[]>("/decisions"),
  backtest: (id: string) => request<RunDetail>(`/backtests/${enc(id)}`),
  costs: () => request<CostConfig>("/costs"),
  source: (key: string) => request<StrategySource>(`/strategies/${enc(key)}/source`),
  signals: (key: string, body: SignalsRequest) =>
    request<Signals>(`/strategies/${enc(key)}/signals`, { method: "POST", body: JSON.stringify(body) }),
  researchList: () => request<ResearchSummary[]>("/research"),
  research: (slug: string) => request<ResearchDetail>(`/research/${enc(slug)}`),
  createResearch: (name: string) =>
    request<ResearchDetail>("/research", { method: "POST", body: JSON.stringify({ name }) }),
  editNote: (slug: string, version: number, sections: Record<string, string>, implementation: string | null) =>
    request<ResearchDetail>(`/research/${enc(slug)}/notes/${version}`, {
      method: "PUT",
      body: JSON.stringify({ sections, implementation }),
    }),
  freezeNote: (slug: string, version: number) =>
    request<ResearchDetail>(`/research/${enc(slug)}/notes/${version}/freeze`, { method: "POST" }),
  newVersion: (slug: string, reason: string) =>
    request<ResearchDetail>(`/research/${enc(slug)}/notes`, { method: "POST", body: JSON.stringify({ reason }) }),
  rejectResearch: (slug: string, reason: string) =>
    request<ResearchDetail>(`/research/${enc(slug)}/reject`, { method: "POST", body: JSON.stringify({ reason }) }),
  chat: (slug: string, message: string) =>
    request<ResearchDetail>(`/research/${enc(slug)}/chat`, { method: "POST", body: JSON.stringify({ message }) }),
  quote: (p: QuoteParams) => request<Quote>(`/costs/quote?${query(p)}`),
  curve: (p: Omit<QuoteParams, "notional">) => request<CostCurve>(`/costs/curve?${query(p)}`),
};
