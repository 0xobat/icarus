import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/server/db";

export async function GET(request: NextRequest) {
  const limitParam = request.nextUrl.searchParams.get("limit");
  const limit = Math.min(Math.max(parseInt(limitParam ?? "10", 10) || 10, 1), 50);

  const rows = await query(
    `SELECT id, tx_hash, timestamp, type, strategy_id, description, value, status
     FROM trades
     ORDER BY timestamp DESC
     LIMIT $1`,
    [limit],
  );

  const executions = rows.map((row) => ({
    id: row.id,
    tx_hash: row.tx_hash ?? undefined,
    timestamp: row.timestamp,
    type: row.type,
    strategy_id: row.strategy_id,
    description: row.description,
    value: parseFloat(row.value),
    status: row.status,
  }));

  return NextResponse.json({ data: executions });
}
