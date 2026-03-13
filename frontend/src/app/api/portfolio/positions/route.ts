import { NextResponse } from "next/server";
import { query } from "@/lib/server/db";

export async function GET() {
  const rows = await query(
    `SELECT strategy_id, strategy_name, protocol, asset, amount, entry_price,
            current_value, unrealized_pnl, unrealized_pnl_pct, portfolio_pct,
            entry_timestamp, tx_hash
     FROM portfolio_positions
     WHERE status = 'open'
     ORDER BY current_value DESC`,
  );

  const positions = rows.map((row) => ({
    strategy_id: row.strategy_id,
    strategy_name: row.strategy_name,
    protocol: row.protocol,
    asset: row.asset,
    amount: parseFloat(row.amount),
    entry_price: parseFloat(row.entry_price),
    current_value: parseFloat(row.current_value),
    unrealized_pnl: parseFloat(row.unrealized_pnl),
    unrealized_pnl_pct: parseFloat(row.unrealized_pnl_pct),
    portfolio_pct: parseFloat(row.portfolio_pct),
    entry_timestamp: row.entry_timestamp,
    tx_hash: row.tx_hash,
  }));

  return NextResponse.json({ data: positions });
}
