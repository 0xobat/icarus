import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/server/db";

export async function GET(request: NextRequest) {
  const limitParam = request.nextUrl.searchParams.get("limit");
  const limit = Math.min(Math.max(parseInt(limitParam ?? "10", 10) || 10, 1), 50);

  let rows;
  try {
    rows = await query(
      `SELECT id, trade_id, tx_hash, timestamp, action, strategy, protocol,
              asset_in, amount_in, status, gas_used, error_message
       FROM trades
       ORDER BY timestamp DESC
       LIMIT $1`,
      [limit],
    );
  } catch {
    return NextResponse.json({ error: "Database error" }, { status: 500 });
  }

  const executions = rows.map((row) => ({
    id: row.id,
    trade_id: row.trade_id,
    tx_hash: row.tx_hash ?? undefined,
    timestamp: row.timestamp,
    type: row.action,
    strategy_id: row.strategy,
    description: `${row.action} ${row.asset_in ?? ""} on ${row.protocol ?? ""}`.trim(),
    value: parseFloat(row.amount_in ?? "0"),
    status: row.status,
  }));

  return NextResponse.json({ data: executions });
}
