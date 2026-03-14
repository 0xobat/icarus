import { NextResponse } from "next/server";
import { query } from "@/lib/server/db";

const STRATEGY_NAMES: Record<string, string> = {
  "LEND-001": "Aave Lending",
  "LP-001": "Aerodrome LP",
};

export async function GET() {
  let rows;
  try {
    rows = await query(
      `SELECT strategy, protocol, asset, amount, entry_price,
              current_value, unrealized_pnl, entry_time
       FROM portfolio_positions
       WHERE status = 'open'
       ORDER BY current_value DESC`,
    );
  } catch {
    return NextResponse.json({ error: "Database error" }, { status: 500 });
  }

  // Compute total value for portfolio_pct
  const totalValue = rows.reduce(
    (sum, row) => sum + parseFloat(row.current_value || "0"),
    0,
  );

  const positions = rows.map((row) => {
    const currentValue = parseFloat(row.current_value || "0");
    const entryPrice = parseFloat(row.entry_price || "0");
    const amount = parseFloat(row.amount || "0");
    const unrealizedPnl = parseFloat(row.unrealized_pnl || "0");
    const costBasis = amount * entryPrice;

    return {
      strategy_id: row.strategy,
      strategy_name: STRATEGY_NAMES[row.strategy] ?? row.strategy,
      protocol: row.protocol,
      asset: row.asset,
      amount,
      entry_price: entryPrice,
      current_value: currentValue,
      unrealized_pnl: unrealizedPnl,
      unrealized_pnl_pct: costBasis > 0 ? (unrealizedPnl / costBasis) * 100 : 0,
      portfolio_pct: totalValue > 0 ? (currentValue / totalValue) * 100 : 0,
      entry_timestamp: row.entry_time,
      tx_hash: null,
    };
  });

  return NextResponse.json({ data: positions });
}
