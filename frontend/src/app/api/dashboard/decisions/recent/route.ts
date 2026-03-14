import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/server/db";

export async function GET(request: NextRequest) {
  const limitParam = request.nextUrl.searchParams.get("limit");
  const limit = Math.min(Math.max(parseInt(limitParam ?? "10", 10) || 10, 1), 50);

  const rows = await query(
    `SELECT correlation_id, timestamp, decision_action, reasoning, orders_json
     FROM decision_audit_log
     ORDER BY timestamp DESC
     LIMIT $1`,
    [limit],
  );

  const decisions = rows.map((row) => {
    let orders: unknown[] = [];
    try {
      orders = typeof row.orders_json === "string" ? JSON.parse(row.orders_json) : row.orders_json ?? [];
    } catch { /* ignore */ }

    return {
      id: row.correlation_id,
      timestamp: row.timestamp,
      action: row.decision_action,
      summary: row.reasoning,
      reasoning: row.reasoning,
      order_count: Array.isArray(orders) ? orders.length : 0,
    };
  });

  return NextResponse.json({ data: decisions });
}
