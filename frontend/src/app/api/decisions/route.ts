import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/server/db";

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const limitParam = params.get("limit");
  const limit = Math.min(Math.max(parseInt(limitParam ?? "20", 10) || 20, 1), 100);
  const cursor = params.get("cursor");
  const strategy = params.get("strategy");
  const actionParam = params.get("action");
  const sourceParam = params.get("source");

  const conditions: string[] = [];
  const values: unknown[] = [];
  let paramIndex = 1;

  if (cursor) {
    conditions.push(`correlation_id < $${paramIndex}`);
    values.push(cursor);
    paramIndex++;
  }

  if (strategy) {
    conditions.push(`trigger_reports @> $${paramIndex}::jsonb`);
    values.push(JSON.stringify([{ strategy_id: strategy }]));
    paramIndex++;
  }

  if (actionParam) {
    const actions = actionParam.split(",").map((a) => a.trim()).filter(Boolean);
    if (actions.length > 0) {
      const placeholders = actions.map((_, i) => `$${paramIndex + i}`).join(", ");
      conditions.push(`action IN (${placeholders})`);
      values.push(...actions);
      paramIndex += actions.length;
    }
  }

  if (sourceParam) {
    const sources = sourceParam.split(",").map((s) => s.trim()).filter(Boolean);
    if (sources.length > 0) {
      const placeholders = sources.map((_, i) => `$${paramIndex + i}`).join(", ");
      conditions.push(`source IN (${placeholders})`);
      values.push(...sources);
      paramIndex += sources.length;
    }
  }

  const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

  // Fetch limit + 1 to determine has_more
  values.push(limit + 1);
  const rows = await query(
    `SELECT correlation_id, timestamp, source, action, summary, reasoning,
            trigger_reports, orders, verification
     FROM decision_audit_log
     ${whereClause}
     ORDER BY correlation_id DESC
     LIMIT $${paramIndex}`,
    values,
  );

  const hasMore = rows.length > limit;
  const pageRows = hasMore ? rows.slice(0, limit) : rows;

  const data = pageRows.map((row) => ({
    id: row.correlation_id,
    timestamp: row.timestamp,
    source: row.source,
    action: row.action,
    summary: row.summary,
    reasoning: row.reasoning,
    trigger_reports: row.trigger_reports,
    orders: row.orders,
    verification: row.verification,
    executions: [],
  }));

  const nextCursor = hasMore && pageRows.length > 0
    ? pageRows[pageRows.length - 1].correlation_id
    : null;

  return NextResponse.json({
    data,
    next_cursor: nextCursor,
    has_more: hasMore,
  });
}
