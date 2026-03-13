import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/server/db";

export async function GET(request: NextRequest) {
  const limitParam = request.nextUrl.searchParams.get("limit");
  const limit = Math.min(Math.max(parseInt(limitParam ?? "10", 10) || 10, 1), 50);

  const rows = await query(
    `SELECT correlation_id, timestamp, action, summary, reasoning, orders
     FROM decision_audit_log
     ORDER BY timestamp DESC
     LIMIT $1`,
    [limit],
  );

  const decisions = rows.map((row) => ({
    id: row.correlation_id,
    timestamp: row.timestamp,
    action: row.action,
    summary: row.summary,
    reasoning: row.reasoning,
    order_count: Array.isArray(row.orders) ? row.orders.length : 0,
  }));

  return NextResponse.json({ data: decisions });
}
