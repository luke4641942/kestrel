import type { Metrics, RunDetail } from "../api";

export function metrics(over: Partial<Metrics> = {}): Metrics {
  return {
    start: "2022-01-01T00:00:00Z",
    end: "2025-11-01T00:00:00Z",
    days: 1400,
    final_equity: 400,
    total_return: 400 / 330 - 1,
    cagr: 0.05,
    volatility: 0.5,
    sharpe: 0.6,
    sharpe_ci: [-0.4, 1.6],
    psr: 0.9,
    sortino: 0.8,
    max_drawdown: -0.3,
    longest_underwater_days: 200,
    skew: 0,
    kurtosis: 3,
    exposure: 0.5,
    trades: 50,
    rejections: 0,
    band_skips: 0,
    fees: 1,
    spread_costs: 0.1,
    impact_costs: 0.01,
    total_costs: 1.11,
    turnover: 2,
    few_trades: false,
    ...over,
  };
}

export function run(over: Partial<RunDetail> = {}, m: Partial<Metrics> = {}, btcFinal = 700): RunDetail {
  return {
    id: "20260101-000000-abcdef",
    created_at: "2026-01-01T00:00:00Z",
    strategy: "ma-trend",
    strategy_kind: "engine-test",
    style: "weights",
    params: {},
    research: null,
    strategy_name: "moving-average trend 20/100 BTCUSDT",
    symbol: "BTCUSDT",
    interval: "4h",
    start: "2022-01",
    end: "2025-10",
    initial_cash: "330",
    account_currency: "USDT",
    used_holdout: false,
    config_fingerprint: "abc123def456",
    code: { commit: "deadbeef", dirty: false },
    metrics: metrics(m),
    benchmark_metrics: metrics({ final_equity: btcFinal }),
    config: {},
    equity: { time: [], equity: [], drawdown: [] },
    benchmark: { time: [], equity: [], drawdown: [] },
    fills: [],
    rejections: [],
    trades: [],
    ...over,
  };
}

export function trade(r: number, entry = "2023-01-01T00:00:00Z", exit = "2023-01-05T00:00:00Z") {
  return {
    entry_time: entry,
    exit_time: exit,
    exit_reason: (r > 0 ? "target" : "stop") as "target" | "stop",
    quantity: 0.001,
    entry_price: 100,
    exit_price: 100 + r * 5,
    stop: 95,
    target: 115,
    risk: 5,
    pnl: r * 5,
    costs: 0.1,
    r_multiple: r,
    cost_r: 0.02,
    risk_capped: false,
  };
}
