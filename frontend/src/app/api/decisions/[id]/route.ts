import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/server/db";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  const rows = await query(
    `SELECT correlation_id, timestamp, decision_action, reasoning,
            strategy_reports_json, orders_json, passed_verification, risk_flags_json
     FROM decision_audit_log
     WHERE correlation_id = $1`,
    [id],
  );

  if (rows.length === 0) {
    return NextResponse.json({ error: "Decision not found" }, { status: 404 });
  }

  const row = rows[0];

  let orders: unknown[] = [];
  let triggerReports: unknown[] = [];
  let riskFlags: unknown = null;
  try { orders = typeof row.orders_json === "string" ? JSON.parse(row.orders_json) : row.orders_json ?? []; } catch { /* ignore */ }
  try { triggerReports = typeof row.strategy_reports_json === "string" ? JSON.parse(row.strategy_reports_json) : row.strategy_reports_json ?? []; } catch { /* ignore */ }
  try { riskFlags = typeof row.risk_flags_json === "string" ? JSON.parse(row.risk_flags_json) : row.risk_flags_json; } catch { /* ignore */ }

  // Fetch related executions from trades table by correlation_id
  const tradeRows = await query(
    `SELECT trade_id, tx_hash, status, amount_in, gas_used, gas_price_wei
     FROM trades
     WHERE correlation_id = $1`,
    [row.correlation_id],
  );

  const executions = tradeRows.map((t) => ({
    tx_hash: t.tx_hash,
    status: t.status,
    gas_cost_usd: 0,
    value: parseFloat(t.amount_in ?? "0"),
  }));

  const decision = {
    id: row.correlation_id,
    timestamp: row.timestamp,
    source: "claude",
    action: row.decision_action,
    summary: row.reasoning,
    reasoning: row.reasoning,
    trigger_reports: triggerReports,
    orders,
    verification: { passed: row.passed_verification, checks: riskFlags },
    executions,
  };

  return NextResponse.json({ data: decision });
}
