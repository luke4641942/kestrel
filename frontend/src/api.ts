// Typed client for the Kestrel API. Types mirror src/kestrel/api/models.py
// (the OpenAPI schema at /api/openapi.json is the source of truth).
import { DEMO, demoRequest } from "./demo";

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

/** Preset key -> line name -> one value per bar (null while warming up). */
export type IndicatorLines = Record<string, Record<string, (number | null)[]>>;

/** One entry of the indicator menu (kestrel.indicators.PRESETS). */
export interface IndicatorInfo {
  key: string;
  label: string;
  /** "price": over the candles; "own": its own pane underneath. */
  pane: "price" | "own";
  description: string;
  lines: string[];
  guides: number[];
  percent: boolean;
  /** How it's worked out and how people read it, in plain English. */
  reading: string;
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
  indicators?: IndicatorLines;
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
  indicators?: string[];
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
  indicators?: IndicatorLines;
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
  /** crypto, forex or futures, read from the implementation. */
  asset_class?: string;
}

/** What to do with a tested strategy (research/decisions.toml). */
export interface Decision {
  strategy: string;
  name: string;
  /** The instrument ("BTCUSDT 1d"); the market it trades in is `asset_class`. */
  market: string;
  asset_class?: string;
  /** needs-shorting: a hypothetical shorting study that passed; never traded. */
  decision: "pursue" | "watch" | "needs-shorting" | "drop";
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
  /** Trials per pool (main is crypto, confirmation, forex); `trials` is main's. */
  pools?: Record<string, number>;
  /** Every trial in every pool: reported beside each bar. */
  all_trials?: number;
  /** Each pool's luck bar (annual Sharpe). */
  pool_bars?: Record<string, number>;
}

/** A saved forex run (GET /fx-runs), deflated against the forex pool. */
export interface FxRun {
  id: string;
  created_at: string;
  strategy: string;
  /** The pairs, comma separated. */
  symbol: string;
  start: string;
  end: string;
  initial_cash: number;
  account_currency: string;
  research: { slug: string; version: number } | null;
  params: Record<string, number>;
  metrics: {
    sharpe: number;
    total_return: number;
    max_drawdown: number;
    average_gross: number;
    financing: number;
    spread_costs: number;
    trades: number;
    windows: number;
    windows_positive: number;
    wiped_out: boolean;
  };
  deflated_sharpe: number | null;
  deflated_trials: number;
  asset_class: string;
}

/** The trial log after one saved run or study (GET /validation/history). */
export interface LuckPoint {
  at: string;
  trials: number;
  /** Annual Sharpe the best of that many worthless strategies would reach by luck. */
  luck_bar: number;
}

/** How every strategy is judged (kestrel.validation.rules) and the lessons so far. */
export interface Rules {
  reviewed: string;
  /** What a pass means. */
  passing: string;
  scorecard: { name: string; rule: string; why: string }[];
  risk_managed: string;
  entry_bar: string[];
  account: string;
  /** From research/findings.md. */
  lessons: { title: string; text: string }[];
}

/** A saved regime fit (kestrel.regimes.RegimeStore), without its daily series. */
export interface RegimeSummary {
  symbol: string;
  created_at: string;
  first: string | null;
  last: string | null;
  /** Monthly refits: one model per month, fitted on the four years before it. */
  refits: number;
}

export interface RegimeState {
  /** 0 is the calmest (states are sorted by volatility after every fit). */
  state: number;
  /** Share of fitted days on which this was the most likely state. */
  share: number;
  /** Average daily log return on those days. */
  mean_return: number | null;
  /** Annualised volatility on those days. */
  volatility: number | null;
  /** Average unbroken spell, in days. */
  average_run: number | null;
  /** From the latest model's switching odds. */
  expected_days: number;
}

export interface RegimeDetail extends RegimeSummary {
  preset: Record<string, number>;
  /** Epoch seconds, one per daily bar. */
  time: number[];
  close: number[];
  /** Per day, P(each state) as the model believed it that day; null before the first fit. */
  probabilities: (number[] | null)[];
  state: (number | null)[];
  states: RegimeState[];
  /** Latest model: row = today, column = tomorrow. */
  transitions: number[][] | null;
  oos_log_likelihood: number;
}

export interface HoldoutScore {
  sharpe: number | null;
  total_return: number;
  max_drawdown: number;
}

/** One market and session in a cost gate: S2 stop widths against a round trip's costs. */
export interface CostGateRow {
  /** e.g. "BTCUSDT 1m, NY open". */
  market: string;
  setups: number;
  p25_bps: number | null;
  median_bps: number | null;
  p75_bps: number | null;
  /** The median setup's round-trip cost in R. */
  cost_r: number | null;
  share_over_half_r: number | null;
  /** Win rate needed to break even after costs, keyed by target in R ("2", "4"). */
  break_even: Record<string, number | null>;
  /** CARRIES, MARGINAL, FAILS or NO SETUPS. */
  verdict: string;
  /** The setup's own target in R (median), when it names one (order flow's point of control). */
  target_r: number | null;
}

/** An event study (kestrel.studies.events), run once from its frozen note with kestrel
 * event-study: what coins did around an event, against the other coins. */
export interface EventStudy {
  name: string;
  note: string;
  measured_at: string;
  commit: string | null;
  /** "EXIT RULE", "KILL" or "INSUFFICIENT". */
  verdict: string;
  events: number;
  event_days: number;
  /** The note's primary window, mean over event days. */
  mean: number;
  p_below: number;
  share_negative: number;
  placebo_median: number | null;
  placebo_share_at_or_below: number;
  tests: Record<string, boolean>;
  /** Mean abnormal return between each day's open and day 0's: 0 at the event. */
  path: { day: number; abnormal: number | null }[];
  report: string;
}

export interface SignalSide {
  n_on: number;
  n_off: number;
  /** Mean log return of the weeks after the signal was on. */
  mean_on: number | null;
  mean_off: number | null;
  spread: number | null;
  p_positive: number | null;
}

export interface SignalPart {
  /** "PROCEED" or "KILL". */
  verdict: string;
  markets: string[];
  /** One round trip of costs, as a log return. */
  hurdle: number;
  weeks: SignalSide;
  past_week_rose: Record<string, { on: number | null; off: number | null }>;
  /** The pre-registered control (newer studies only): the spread after a rising week. */
  after_rise: SignalSide | null;
  after_fall: SignalSide | null;
  control_holds: boolean | null;
}

/** A weekly signal study (kestrel.studies.weekly_signal), run from its frozen note with
 * kestrel signal-study: do the weeks after a signal beat the rest? Not a strategy. */
export interface SignalStudy extends SignalPart {
  signal: string;
  title: string;
  note: string;
  measured_at: string;
  commit: string | null;
  /** A result its note reports alongside, deciding nothing. */
  beside: SignalPart | null;
  report: string;
}

/** A hypothetical shorting study (kestrel.studies.shorting), run once from its frozen note
 * with kestrel short-study. Not an engine run: Kestrel is spot and long-only. */
export interface ShortStudy {
  name: string;
  note: string;
  measured_at: string;
  commit: string | null;
  /** "NEEDS-SHORTING" (passed; untradeable until shorting is possible) or "DROP". */
  verdict: string;
  tests: Record<string, boolean>;
  /** The full report, as the CLI printed it. */
  report: string;
  /** Growth of 1 (1.0 at the start). */
  curves: { label: string; series: EquitySeries }[];
}

/** A saved cost gate (kestrel.studies.cost_gate). Stops only, no outcomes; run from the CLI. */
export interface CostGate {
  interval: Interval;
  /** "fvg" (S2/S3 fair value gaps) or "order-flow". */
  setup: string;
  measured_at: string;
  first: string;
  last: string;
  round_trip_bps: number | null;
  carries_at: number;
  fails_above: number;
  rows: CostGateRow[];
}

/** One recorded holdout test (kestrel.validation.holdout). Read-only: run from the CLI only. */
export interface HoldoutRecord {
  research: string;
  version: number;
  recorded_at: string;
  strategy: string;
  symbol: string;
  start: string;
  /** Exclusive. */
  end: string;
  exposure: number;
  mine: HoldoutScore;
  held: HoldoutScore;
  matched_return: number;
  falling_year: boolean;
  edge_passes: boolean;
  drawdown_passes: boolean;
  passes: boolean;
}

/** A paper account at a glance (kestrel.execution.paper). Opened and run from the CLI. */
export interface PaperSummary {
  name: string;
  strategy: string;
  symbol: string;
  interval: Interval;
  /** "sim" (simulated fills) or "testnet" (Binance Spot Testnet orders). */
  venue: string;
  research: string | null;
  initial_cash: number;
  trade_from: string;
  last_bar: string | null;
  /** At the latest mark; null before the first. */
  equity: number | null;
  cash: number;
  position: number;
  /** A basket account's coins by symbol; empty for a single-coin account. */
  positions: Record<string, number>;
  /** Why it is halted, or null. */
  halted: string | null;
  /** Trades (a coin from flat back to flat), open ones included. */
  trade_count: number;
  /** Its entry tests at the latest stored close; null if the strategy doesn't describe them. */
  waiting: Waiting | null;
  /** What holding its market made over the same days, one entry's costs paid (a basket: its
   * coins in equal parts). A fraction; null before the first mark. */
  hold_return?: number | null;
}

/** One entry test at the latest close (kestrel.strategies.conditions). */
export interface Condition {
  label: string;
  /** Whose close it tests. */
  subject: string;
  value: number;
  level: number;
  met: boolean;
  /** Change in the subject's close by the next close that flips the test (fraction). */
  move: number;
}

export interface Waiting {
  bar: string;
  /** "all": in the market when every test holds; "any": a share for each that holds. */
  rule: "all" | "any";
  /** "every close" or "Sunday closes". */
  decides: string;
  /** A rotation's picks if its tests held now. */
  would_hold: string[];
  conditions: Condition[];
}

/** One trade: a coin from flat back to flat; resizes in between belong to it. */
export interface PaperTrade {
  symbol: string;
  opened: string;
  /** Null while still held. */
  closed: string | null;
  /** Spent on buys, fees included (USDT). */
  bought: number;
  /** Received from sells, fees taken off (USDT). */
  sold: number;
  /** An open trade's coins at the latest close (USDT). */
  value: number | null;
  /** Closed: sold less bought. Open: unrealised, at the latest close. */
  profit: number | null;
  fees: number;
  fills: number;
}

/** Every paper account added up, against the model's accounts added up. */
export interface PaperTotal {
  accounts: number;
  /** Every account's starting cash, added up (USDT). */
  initial_cash: number;
  /** At the latest mark; null before any. */
  equity: number | null;
  expected_equity: number | null;
  /** Equity less starting cash. */
  profit: number | null;
  equity_series: EquitySeries;
  expected: EquitySeries;
}

export interface PaperDetail extends PaperSummary {
  equity_series: EquitySeries;
  /** The same account replayed on the simulated venue: the backtest model. */
  expected: EquitySeries;
  fills: {
    at: string;
    symbol: string;
    side: Side;
    quantity: number;
    reference_price: number;
    price: number;
    fee: number;
    cost_bps: number;
    expected_bps: number | null;
  }[];
  /** Oldest first. */
  trades: PaperTrade[];
  /** Newest first. */
  decisions: {
    bar: string;
    /** Total target weight (a basket's weights added up). */
    target: number;
    /** Weight held before; null for a basket. */
    current: number | null;
    missed: boolean;
    band_skip: boolean;
    /** A basket's targets by symbol (null: keep as held). */
    targets?: Record<string, number | null> | null;
  }[];
  events: { type: "halt" | "resume" | "kill"; at: string; reason: string | null }[];
  tracking: {
    days: number;
    decision_mismatches: number;
    missed: number;
    /** Final equity against the model's, as a fraction. */
    difference: number | null;
    cost_bps: number | null;
    expected_bps: number | null;
  };
  /** The decay check (kestrel.validation.decay); null if it couldn't be made. */
  decay?: Decay | null;
}

/** The model replay's Sharpe against its own backtest's: information only. */
export interface Decay {
  days: number;
  min_days: number;
  percentile: number;
  sharpe: number | null;
  floor: number | null;
  median: number | null;
  status: "too early" | "in line" | "flagged";
}

export interface Study {
  id: string;
  created_at: string;
  kind: "robustness" | "walk-forward" | "random-entry" | "confirmation";
  strategy: string;
  symbol: string;
  interval: Interval;
  start: string;
  end: string;
  looks: { params: Record<string, number>; sharpe: number | null }[];
  /** ROBUST, FRAGILE or NO EDGE (robustness); SURVIVES or KILL (walk-forward); BEATS RANDOM,
   * NO BETTER THAN RANDOM or NO TRADES (random-entry); PASS or FAIL (confirmation). */
  verdict: string;
  /** What the study showed; for a confirmation run, its per-coin table (ConfirmationDetail). */
  detail?: Record<string, unknown>;
  /** A confirmation run of the exploration experiment, counted in its own trial pool. */
  confirmation?: boolean;
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

export interface StartDateRow {
  window_days: number;
  windows: number;
  sharpe: number | null;
  drawdown: number | null;
  total_return: number | null;
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
  /** Max drawdown on resampled paths: [median, 95th percentile worst]; absent on older runs. */
  drawdown_paths?: number[] | null;
  start_dates: StartDateRow[];
  /** How much of the return the best days and best year carry; holding's for comparison. */
  concentration?: Concentration | null;
  hold_concentration?: Concentration | null;
}

export interface Concentration {
  total_return: number;
  /** The return with the top_days best days taken out. */
  without_top_days: number | null;
  top_days: number;
  best_year: number | null;
  best_year_return: number | null;
  /** The return with the best calendar year taken out. */
  without_best_year: number | null;
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
  /** null for buy-and-hold and holdout runs, which aren't graded. */
  scorecard: Scorecard | null;
}

/** n/a: robustness, for a strategy with no settings to tune. */
export type GradeStatus = "pass" | "fail" | "not run" | "n/a";

/** The run graded against the scorecard (kestrel.validation.scorecard). */
export interface Scorecard {
  /** pass: all eight pass or don't apply; fail: any fails; incomplete: a study is missing. */
  verdict: "pass" | "fail" | "incomplete";
  failed: number;
  not_run: number;
  grades: { name: string; rule: string; status: GradeStatus; measured: string }[];
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
  if (DEMO) return demoRequest<T>(path, init);
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
  bars: (symbol: string, interval: Interval, limit = 2000, indicators: string[] = []) =>
    request<Bars>(
      `/datasets/${enc(symbol)}/${interval}/bars?limit=${limit}${indicators.map((k) => `&indicators=${enc(k)}`).join("")}`,
    ),
  indicators: () => request<IndicatorInfo[]>("/indicators"),
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
  validationHistory: () => request<LuckPoint[]>("/validation/history"),
  decisions: () => request<Decision[]>("/decisions"),
  fxRuns: () => request<FxRun[]>("/fx-runs"),
  paper: () => request<PaperSummary[]>("/paper"),
  paperTotal: () => request<PaperTotal>("/paper-total"),
  paperAccount: (name: string) => request<PaperDetail>(`/paper/${enc(name)}`),
  holdout: () => request<HoldoutRecord[]>("/holdout"),
  costGate: () => request<CostGate[]>("/cost-gate"),
  shortStudies: () => request<ShortStudy[]>("/short-studies"),
  eventStudies: () => request<EventStudy[]>("/event-studies"),
  signalStudies: () => request<SignalStudy[]>("/signal-studies"),
  regimes: () => request<RegimeSummary[]>("/regimes"),
  regime: (symbol: string) => request<RegimeDetail>(`/regimes/${enc(symbol)}`),
  startRegimes: (symbol: string) => request<Job>("/jobs/regimes", { method: "POST", body: JSON.stringify({ symbol }) }),
  rules: () => request<Rules>("/rules"),
  summary: () => request<{ markdown: string }>("/summary"),
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
