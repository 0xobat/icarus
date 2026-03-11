/** Data contracts matching spec §4–7. All fields mirror the spec's data contract blocks. */

export interface HoldModeData {
  active: boolean;
  reason: string;
  since: string; // ISO8601
}

export interface DecisionLoopEvent {
  type: "eval" | "claude_call" | "tx_exec";
  timestamp: string;
  strategy_id?: string;
}

export interface MetricsData {
  portfolio_value: number;
  portfolio_change_24h_pct: number;
  portfolio_change_24h_abs: number;
  portfolio_sparkline: number[];
  drawdown_current: number;
  drawdown_limit: number;
  pnl_today: number;
  pnl_today_pct: number;
  pnl_sparkline: number[];
  tx_success_rate: number;
  tx_success_count: number;
  tx_total_count: number;
}

export interface StrategyData {
  id: string;
  name: string;
  status: "active" | "inactive";
  allocation: number;
  allocation_pct: number;
  pnl: number;
  pnl_pct: number;
  last_eval_ago: string;
  active_signals: number;
}

export interface StrategiesPanelData {
  strategies: StrategyData[];
  reserve: { amount: number; pct: number };
  total_value: number;
}

export interface Execution {
  id: string;
  tx_hash?: string;
  timestamp: string;
  type: "entry" | "exit" | "harvest" | "rebalance";
  strategy_id: string;
  description: string;
  value: number;
  status: "success" | "pending" | "failed";
}

export interface CircuitBreaker {
  name: string;
  current: number;
  limit: number;
  unit: string;
  status: "safe" | "warning" | "critical" | "triggered";
  last_triggered: string | null;
}

export interface ClaudeDecision {
  id: string;
  timestamp: string;
  action: "ENTRY" | "EXIT" | "REBALANCE" | "HOLD";
  summary: string;
  reasoning: string;
  order_count: number;
}

export interface ChartPoint {
  timestamp: string;
  value: number;
}

// Portfolio page (spec §5)
export interface Position {
  strategy_id: string;
  strategy_name: string;
  protocol: string;
  asset: string;
  amount: number;
  entry_price: number;
  current_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  portfolio_pct: number;
  entry_timestamp: string;
  tx_hash: string;
}

export interface ReserveData {
  liquid_reserve: number;
  min_reserve_requirement: number;
  reserve_pct: number;
}

// Decisions page (spec §6)
export interface DecisionDetail {
  id: string;
  timestamp: string;
  source: "claude" | "circuit_breaker";
  action: string;
  summary: string;
  reasoning: string;
  trigger_reports: Array<{ strategy_id: string; signals: string[] }>;
  orders: Array<{
    action: string;
    protocol: string;
    asset: string;
    amount: number;
    parameters: Record<string, unknown>;
  }>;
  verification: { passed: boolean; checks: string[] };
  executions: Array<{
    tx_hash: string;
    status: "success" | "pending" | "failed";
    gas_cost_usd: number;
    value: number;
  }>;
}

// Risk page (spec §7)
export interface ExposureLimit {
  scope: "protocol" | "asset";
  name: string;
  current_allocation: number;
  current_pct: number;
  limit_pct: number;
  headroom: number;
}

export interface ServiceHealth {
  name: string;
  status: "connected" | "disconnected" | "degraded";
  latency_ms: number | null;
  last_heartbeat: string;
  error_count_24h: number;
}
