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
  instruments: { symbol: string; step_size: number; min_qty: number; min_notional: number }[];
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
  costs: () => request<CostConfig>("/costs"),
  quote: (p: QuoteParams) => request<Quote>(`/costs/quote?${query(p)}`),
  curve: (p: Omit<QuoteParams, "notional">) => request<CostCurve>(`/costs/curve?${query(p)}`),
};
