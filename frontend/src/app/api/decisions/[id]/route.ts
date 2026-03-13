import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/server/db";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  const rows = await query(
    `SELECT correlation_id, timestamp, source, action, summary, reasoning,
            trigger_reports, orders, verification
     FROM decision_audit_log
     WHERE correlation_id = $1`,
    [id],
  );

  if (rows.length === 0) {
    return NextResponse.json({ error: "Decision not found" }, { status: 404 });
  }

  const row = rows[0];

  // Fetch related executions from trades table
  const orderIds = Array.isArray(row.orders)
    ? row.orders.map((_: unknown, i: number) => `${row.correlation_id}-${i}`)
    : [];

  let executions: Array<{
    tx_hash: string;
    status: string;
    gas_cost_usd: number;
    value: number;
  }> = [];

  if (orderIds.length > 0) {
    const placeholders = orderIds.map((_: string, i: number) => `$${i + 1}`).join(", ");
    const tradeRows = await query(
      `SELECT tx_hash, status, value, gas_used, effective_gas_price
       FROM trades
       WHERE order_id IN (${placeholders})`,
      orderIds,
    );

    executions = tradeRows.map((t) => ({
      tx_hash: t.tx_hash,
      status: t.status,
      gas_cost_usd: 0, // Gas cost in USD computed client-side from ETH price
      value: parseFloat(t.value),
    }));
  }

  const decision = {
    id: row.correlation_id,
    timestamp: row.timestamp,
    source: row.source,
    action: row.action,
    summary: row.summary,
    reasoning: row.reasoning,
    trigger_reports: row.trigger_reports,
    orders: row.orders,
    verification: row.verification,
    executions,
  };

  return NextResponse.json({ data: decision });
}
