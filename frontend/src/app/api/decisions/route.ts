import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/server/db";

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const limitParam = params.get("limit");
  const limit = Math.min(Math.max(parseInt(limitParam ?? "20", 10) || 20, 1), 100);
  const cursor = params.get("cursor");
  const strategy = params.get("strategy");
  const actionParam = params.get("action");

  const conditions: string[] = [];
  const values: unknown[] = [];
  let paramIndex = 1;

  if (cursor) {
    conditions.push(`correlation_id < $${paramIndex}`);
    values.push(cursor);
    paramIndex++;
  }

  if (strategy) {
    conditions.push(`strategy_reports_json::text LIKE $${paramIndex}`);
    values.push(`%${strategy}%`);
    paramIndex++;
  }

  if (actionParam) {
    const actions = actionParam.split(",").map((a) => a.trim()).filter(Boolean);
    if (actions.length > 0) {
      const placeholders = actions.map((_, i) => `$${paramIndex + i}`).join(", ");
      conditions.push(`decision_action IN (${placeholders})`);
      values.push(...actions);
      paramIndex += actions.length;
    }
  }

  const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

  // Fetch limit + 1 to determine has_more
  values.push(limit + 1);
  const rows = await query(
    `SELECT correlation_id, timestamp, decision_action, reasoning,
            strategy_reports_json, orders_json, passed_verification, risk_flags_json
     FROM decision_audit_log
     ${whereClause}
     ORDER BY correlation_id DESC
     LIMIT $${paramIndex}`,
    values,
  );

  const hasMore = rows.length > limit;
  const pageRows = hasMore ? rows.slice(0, limit) : rows;

  const data = pageRows.map((row) => {
    let orders: unknown[] = [];
    let triggerReports: unknown[] = [];
    let riskFlags: unknown = null;
    try { orders = typeof row.orders_json === "string" ? JSON.parse(row.orders_json) : row.orders_json ?? []; } catch { /* ignore */ }
    try { triggerReports = typeof row.strategy_reports_json === "string" ? JSON.parse(row.strategy_reports_json) : row.strategy_reports_json ?? []; } catch { /* ignore */ }
    try { riskFlags = typeof row.risk_flags_json === "string" ? JSON.parse(row.risk_flags_json) : row.risk_flags_json; } catch { /* ignore */ }

    return {
      id: row.correlation_id,
      timestamp: row.timestamp,
      source: "claude",
      action: row.decision_action,
      summary: row.reasoning,
      reasoning: row.reasoning,
      trigger_reports: triggerReports,
      orders,
      verification: { passed: row.passed_verification, checks: riskFlags },
      executions: [],
    };
  });

  const nextCursor = hasMore && pageRows.length > 0
    ? pageRows[pageRows.length - 1].correlation_id
    : null;

  return NextResponse.json({
    data,
    next_cursor: nextCursor,
    has_more: hasMore,
  });
}
