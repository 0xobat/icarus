// Static mock data for design phase — will be replaced with Redis/Postgres reads

import type {
  HoldModeData,
  DecisionLoopEvent,
  MetricsData,
  StrategyData,
  StrategiesPanelData,
  Execution,
  CircuitBreaker,
  ClaudeDecision,
  ChartPoint,
  Position,
  ReserveData,
  DecisionDetail,
  ExposureLimit,
  ServiceHealth,
} from "@/lib/types";

// ── Hold Mode ───────────────────────────────────

export const holdMode: HoldModeData = {
  active: false,
  reason: "Claude API unavailable — rate limit exceeded",
  since: "2026-03-11T09:42:00Z",
};

// ── Decision Loop Events ────────────────────────

const now = new Date("2026-03-11T14:30:00Z");
function minsAgo(m: number): string {
  return new Date(now.getTime() - m * 60_000).toISOString();
}

export const decisionLoopEvents: DecisionLoopEvent[] = [
  { type: "eval", timestamp: minsAgo(1), strategy_id: "LEND-001" },
  { type: "eval", timestamp: minsAgo(2), strategy_id: "LP-001" },
  { type: "eval", timestamp: minsAgo(3), strategy_id: "LEND-001" },
  { type: "claude_call", timestamp: minsAgo(4) },
  { type: "tx_exec", timestamp: minsAgo(5), strategy_id: "LEND-001" },
  { type: "eval", timestamp: minsAgo(7), strategy_id: "LP-001" },
  { type: "eval", timestamp: minsAgo(8), strategy_id: "LEND-001" },
  { type: "eval", timestamp: minsAgo(10), strategy_id: "LP-001" },
  { type: "claude_call", timestamp: minsAgo(12) },
  { type: "tx_exec", timestamp: minsAgo(13), strategy_id: "LP-001" },
  { type: "eval", timestamp: minsAgo(15), strategy_id: "LEND-001" },
  { type: "eval", timestamp: minsAgo(17), strategy_id: "LP-001" },
  { type: "eval", timestamp: minsAgo(19), strategy_id: "LEND-001" },
  { type: "eval", timestamp: minsAgo(21), strategy_id: "LP-001" },
  { type: "claude_call", timestamp: minsAgo(23) },
  { type: "tx_exec", timestamp: minsAgo(24), strategy_id: "LEND-001" },
  { type: "eval", timestamp: minsAgo(26), strategy_id: "LEND-001" },
  { type: "eval", timestamp: minsAgo(28), strategy_id: "LP-001" },
  { type: "eval", timestamp: minsAgo(30), strategy_id: "LEND-001" },
  { type: "eval", timestamp: minsAgo(33), strategy_id: "LP-001" },
  { type: "eval", timestamp: minsAgo(36), strategy_id: "LEND-001" },
  { type: "claude_call", timestamp: minsAgo(38) },
  { type: "eval", timestamp: minsAgo(40), strategy_id: "LP-001" },
  { type: "eval", timestamp: minsAgo(43), strategy_id: "LEND-001" },
  { type: "eval", timestamp: minsAgo(46), strategy_id: "LP-001" },
  { type: "tx_exec", timestamp: minsAgo(48), strategy_id: "LP-001" },
];

// ── Metrics ─────────────────────────────────────

export const metricsData: MetricsData = {
  portfolio_value: 847_293.42,
  portfolio_change_24h_pct: 2.89,
  portfolio_change_24h_abs: 23_847,
  portfolio_sparkline: [
    820_100, 821_400, 819_800, 822_300, 824_100, 826_700,
    825_200, 828_400, 831_000, 833_200, 830_800, 835_100,
    837_400, 836_200, 839_600, 841_100, 838_900, 840_300,
    842_700, 844_500, 843_200, 845_800, 846_100, 847_293,
  ],
  drawdown_current: 4.2,
  drawdown_limit: 20,
  pnl_today: 23_847,
  pnl_today_pct: 2.89,
  pnl_sparkline: [
    1_200, 2_400, 3_100, 2_800, 4_500, 5_200, 6_800, 8_100,
    9_400, 11_200, 12_800, 14_100, 15_600, 17_300, 19_800, 23_847,
  ],
  tx_success_rate: 98.7,
  tx_success_count: 142,
  tx_total_count: 144,
};

// ── Strategies Panel ────────────────────────────

const lend001: StrategyData = {
  id: "LEND-001",
  name: "Aave V3 Lending Supply",
  status: "active",
  allocation: 412_450,
  allocation_pct: 48.7,
  pnl: 8_234,
  pnl_pct: 2.0,
  last_eval_ago: "12s ago",
  active_signals: 0,
};

const lp001: StrategyData = {
  id: "LP-001",
  name: "Aerodrome Stable LP",
  status: "active",
  allocation: 284_120,
  allocation_pct: 33.5,
  pnl: 15_613,
  pnl_pct: 5.8,
  last_eval_ago: "8s ago",
  active_signals: 1,
};

export const strategiesPanel: StrategiesPanelData = {
  strategies: [lend001, lp001],
  reserve: { amount: 150_723.42, pct: 17.8 },
  total_value: 847_293.42,
};

// ── Executions ──────────────────────────────────

export const executionsData: Execution[] = [
  {
    id: "tx-1",
    tx_hash: "0xa1b2c3...pending",
    timestamp: "2026-03-11T14:28:15Z",
    type: "rebalance",
    strategy_id: "LP-001",
    description: "Rebalancing USDC/USDbC LP on Aerodrome",
    value: 28_450,
    status: "pending",
  },
  {
    id: "tx-2",
    tx_hash: "0xd4e5f6789abc",
    timestamp: "2026-03-11T14:23:42Z",
    type: "harvest",
    strategy_id: "LEND-001",
    description: "Harvested AERO rewards, swapped to USDC",
    value: 847.32,
    status: "success",
  },
  {
    id: "tx-3",
    tx_hash: "0x1a2b3c4d5e6f",
    timestamp: "2026-03-11T14:18:15Z",
    type: "rebalance",
    strategy_id: "LP-001",
    description: "Rebalanced USDC/DAI LP position",
    value: 15_200,
    status: "success",
  },
  {
    id: "tx-4",
    tx_hash: "0x7f8e9d0c1b2a",
    timestamp: "2026-03-11T14:12:03Z",
    type: "entry",
    strategy_id: "LEND-001",
    description: "Supplied USDC to Aave V3 (APY rotation)",
    value: 50_000,
    status: "success",
  },
  {
    id: "tx-5",
    tx_hash: "0x3e4f5a6b7c8d",
    timestamp: "2026-03-11T14:05:51Z",
    type: "exit",
    strategy_id: "LEND-001",
    description: "Withdrew USDbC from Aave V3 (low APY)",
    value: 50_000,
    status: "success",
  },
];

// ── Circuit Breakers ────────────────────────────

export const circuitBreakersData: CircuitBreaker[] = [
  {
    name: "Portfolio Drawdown",
    current: 4.2,
    limit: 20,
    unit: "%",
    status: "safe",
    last_triggered: null,
  },
  {
    name: "Single-Position Loss",
    current: 2.1,
    limit: 10,
    unit: "%",
    status: "safe",
    last_triggered: "2026-02-18T11:32:00Z",
  },
  {
    name: "Gas Spike",
    current: 0.8,
    limit: 3,
    unit: "x avg",
    status: "warning",
    last_triggered: "2026-03-09T22:14:00Z",
  },
  {
    name: "TX Failure Rate",
    current: 1,
    limit: 3,
    unit: "/hr",
    status: "safe",
    last_triggered: "2026-01-05T08:45:00Z",
  },
  {
    name: "Protocol TVL Drop",
    current: 0,
    limit: 30,
    unit: "%/24h",
    status: "safe",
    last_triggered: null,
  },
];

// ── Claude Decisions ────────────────────────────

export const claudeDecisionsData: ClaudeDecision[] = [
  {
    id: "DEC-8942",
    timestamp: "2026-03-11T14:23:38Z",
    action: "REBALANCE",
    summary: "Rebalancing USDC/USDbC LP on Aerodrome",
    reasoning:
      "LP position drifted 1.8% from target range. Gas price favorable at 0.003 gwei on Base. Net benefit after gas exceeds $12.",
    order_count: 1,
  },
  {
    id: "DEC-8941",
    timestamp: "2026-03-11T14:12:00Z",
    action: "ENTRY",
    summary: "Rotated USDC supply from USDbC to USDC market on Aave V3",
    reasoning:
      "USDC supply APY at 4.2% vs USDbC at 3.1%. Differential of 1.1% exceeds 0.5% threshold. Gas cost amortizes in 3 days.",
    order_count: 2,
  },
  {
    id: "DEC-8940",
    timestamp: "2026-03-11T13:48:22Z",
    action: "HOLD",
    summary: "No action — all positions within optimal ranges",
    reasoning:
      "LEND-001 APY stable at 4.2%. LP-001 emissions APR at 6.1%, well above 3.0% entry threshold. No rebalance needed.",
    order_count: 0,
  },
];

// ── Portfolio History (chart points) ────────────

export const portfolioHistoryData: ChartPoint[] = Array.from(
  { length: 24 },
  (_, i) => ({
    timestamp: `${String(i).padStart(2, "0")}:00`,
    value: 820_000 + Math.sin(i / 3) * 8000 + i * 1200 + Math.random() * 3000,
  })
);

// ── Positions ───────────────────────────────────

export const positions: Position[] = [
  {
    strategy_id: "LEND-001",
    strategy_name: "Aave V3 Lending Supply",
    protocol: "Aave V3",
    asset: "USDC",
    amount: 312_450,
    entry_price: 1.0,
    current_value: 318_234,
    unrealized_pnl: 5_784,
    unrealized_pnl_pct: 1.85,
    portfolio_pct: 37.6,
    entry_timestamp: "2026-03-01T10:00:00Z",
    tx_hash: "0xaabb1122...3344",
  },
  {
    strategy_id: "LEND-001",
    strategy_name: "Aave V3 Lending Supply",
    protocol: "Aave V3",
    asset: "USDbC",
    amount: 100_000,
    entry_price: 1.0,
    current_value: 102_450,
    unrealized_pnl: 2_450,
    unrealized_pnl_pct: 2.45,
    portfolio_pct: 12.1,
    entry_timestamp: "2026-03-05T14:30:00Z",
    tx_hash: "0xccdd5566...7788",
  },
  {
    strategy_id: "LP-001",
    strategy_name: "Aerodrome Stable LP",
    protocol: "Aerodrome",
    asset: "USDC/USDbC LP",
    amount: 184_120,
    entry_price: 1.0,
    current_value: 192_847,
    unrealized_pnl: 8_727,
    unrealized_pnl_pct: 4.74,
    portfolio_pct: 22.8,
    entry_timestamp: "2026-02-20T09:15:00Z",
    tx_hash: "0xeeff9900...aabb",
  },
  {
    strategy_id: "LP-001",
    strategy_name: "Aerodrome Stable LP",
    protocol: "Aerodrome",
    asset: "USDC/DAI LP",
    amount: 100_000,
    entry_price: 1.0,
    current_value: 106_886,
    unrealized_pnl: 6_886,
    unrealized_pnl_pct: 6.89,
    portfolio_pct: 12.6,
    entry_timestamp: "2026-02-22T16:45:00Z",
    tx_hash: "0x11223344...5566",
  },
];

// ── Reserve Data ────────────────────────────────

export const reserveData: ReserveData = {
  liquid_reserve: 150_723.42,
  min_reserve_requirement: 84_729.34,
  reserve_pct: 17.8,
};

// ── Decision Details ────────────────────────────

export const decisionDetails: DecisionDetail[] = [
  {
    id: "DEC-8942",
    timestamp: "2026-03-11T14:23:38Z",
    source: "claude",
    action: "REBALANCE",
    summary: "Rebalancing USDC/USDbC LP on Aerodrome",
    reasoning:
      "LP position drifted 1.8% from target range. Gas price favorable at 0.003 gwei on Base. Net benefit after gas exceeds $12.",
    trigger_reports: [
      { strategy_id: "LP-001", signals: ["position_drift > 1.5%", "gas_favorable"] },
    ],
    orders: [
      {
        action: "rebalance",
        protocol: "Aerodrome",
        asset: "USDC/USDbC",
        amount: 28_450,
        parameters: { pool: "0xaero...usdc-usdbc", tickLower: -1, tickUpper: 1 },
      },
    ],
    verification: {
      passed: true,
      checks: ["exposure_limit_ok", "circuit_breaker_ok", "schema_valid"],
    },
    executions: [
      {
        tx_hash: "0xa1b2c3...pending",
        status: "pending",
        gas_cost_usd: 0.02,
        value: 28_450,
      },
    ],
  },
  {
    id: "DEC-8941",
    timestamp: "2026-03-11T14:12:00Z",
    source: "claude",
    action: "ENTRY",
    summary: "Rotated USDC supply from USDbC to USDC market on Aave V3",
    reasoning:
      "USDC supply APY at 4.2% vs USDbC at 3.1%. Differential of 1.1% exceeds 0.5% threshold. Gas cost amortizes in 3 days.",
    trigger_reports: [
      { strategy_id: "LEND-001", signals: ["apy_differential > 0.5%", "gas_amortization < 14d"] },
    ],
    orders: [
      {
        action: "exit",
        protocol: "Aave V3",
        asset: "USDbC",
        amount: 50_000,
        parameters: { market: "USDbC", action: "withdraw" },
      },
      {
        action: "entry",
        protocol: "Aave V3",
        asset: "USDC",
        amount: 50_000,
        parameters: { market: "USDC", action: "supply" },
      },
    ],
    verification: {
      passed: true,
      checks: ["exposure_limit_ok", "circuit_breaker_ok", "schema_valid", "max_allocation_ok"],
    },
    executions: [
      {
        tx_hash: "0x7f8e9d0c1b2a",
        status: "success",
        gas_cost_usd: 0.04,
        value: 50_000,
      },
      {
        tx_hash: "0x3e4f5a6b7c8d",
        status: "success",
        gas_cost_usd: 0.03,
        value: 50_000,
      },
    ],
  },
  {
    id: "DEC-8940",
    timestamp: "2026-03-11T13:48:22Z",
    source: "claude",
    action: "HOLD",
    summary: "No action — all positions within optimal ranges",
    reasoning:
      "LEND-001 APY stable at 4.2%. LP-001 emissions APR at 6.1%, well above 3.0% entry threshold. No rebalance needed.",
    trigger_reports: [
      { strategy_id: "LEND-001", signals: ["apy_stable"] },
      { strategy_id: "LP-001", signals: ["apr_above_threshold", "position_in_range"] },
    ],
    orders: [],
    verification: {
      passed: true,
      checks: ["no_orders_to_verify"],
    },
    executions: [],
  },
];

// ── Exposure Limits ─────────────────────────────

export const exposureLimits: ExposureLimit[] = [
  {
    scope: "protocol",
    name: "Aave V3",
    current_allocation: 412_450,
    current_pct: 48.7,
    limit_pct: 70,
    headroom: 21.3,
  },
  {
    scope: "protocol",
    name: "Aerodrome",
    current_allocation: 284_120,
    current_pct: 33.5,
    limit_pct: 50,
    headroom: 16.5,
  },
  {
    scope: "asset",
    name: "USDC",
    current_allocation: 496_570,
    current_pct: 58.6,
    limit_pct: 80,
    headroom: 21.4,
  },
  {
    scope: "asset",
    name: "USDbC",
    current_allocation: 200_000,
    current_pct: 23.6,
    limit_pct: 50,
    headroom: 26.4,
  },
];

// ── Service Health ──────────────────────────────

export const serviceHealth: ServiceHealth[] = [
  {
    name: "Redis",
    status: "connected",
    latency_ms: 1.2,
    last_heartbeat: "2026-03-11T14:29:58Z",
    error_count_24h: 0,
  },
  {
    name: "PostgreSQL",
    status: "connected",
    latency_ms: 3.8,
    last_heartbeat: "2026-03-11T14:29:57Z",
    error_count_24h: 2,
  },
  {
    name: "Alchemy WS",
    status: "connected",
    latency_ms: 42,
    last_heartbeat: "2026-03-11T14:29:59Z",
    error_count_24h: 0,
  },
  {
    name: "Claude API",
    status: "connected",
    latency_ms: 820,
    last_heartbeat: "2026-03-11T14:29:55Z",
    error_count_24h: 1,
  },
];

// ── Legacy re-exports for existing component imports ─

export const metrics = {
  portfolioValue: metricsData.portfolio_value,
  portfolioChange24h: metricsData.portfolio_change_24h_pct,
  drawdown: -metricsData.drawdown_current,
  drawdownLimit: metricsData.drawdown_limit,
  todayPnl: metricsData.pnl_today,
  pnlChange: metricsData.pnl_today_pct,
  txSuccess: metricsData.tx_success_rate,
  txTotal: metricsData.tx_total_count,
  txSuccessCount: metricsData.tx_success_count,
};

export const strategies = strategiesPanel.strategies.map((s) => ({
  id: s.id,
  name: s.name,
  status: s.status,
  allocation: s.allocation,
  pnl: s.pnl,
  pnlPercent: s.pnl_pct,
  lastEval: s.last_eval_ago,
  signals: s.active_signals,
}));

export const executions = executionsData.map((e) => ({
  id: e.id,
  timestamp: new Date(e.timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }),
  type: e.type,
  strategy: e.strategy_id,
  description: e.description,
  value: e.value,
  status: e.status,
}));

export const circuitBreakers = circuitBreakersData.map((cb) => ({
  name: cb.name,
  current: cb.current,
  limit: cb.limit,
  unit: cb.unit,
  status: cb.status as "safe" | "warning" | "critical",
}));

export const portfolioHistory = portfolioHistoryData.map((p) => ({
  hour: p.timestamp,
  value: p.value,
}));

export const claudeDecisions = claudeDecisionsData.map((d) => ({
  id: d.id,
  timestamp: new Date(d.timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }),
  action: d.action,
  summary: d.summary,
  reasoning: d.reasoning,
  orders: d.order_count,
}));
