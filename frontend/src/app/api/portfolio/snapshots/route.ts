import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/server/db";

const RANGE_INTERVALS: Record<string, string | null> = {
  "24h": "1 day",
  "7d": "7 days",
  "1m": "30 days",
  "3m": "90 days",
  all: null,
};

function toSnapshots(rows: Record<string, unknown>[]) {
  return rows.map((row) => ({
    timestamp: row.timestamp,
    total_value: parseFloat(String(row.total_value_usd ?? "0")),
  }));
}

export async function GET(request: NextRequest) {
  const range = request.nextUrl.searchParams.get("range") ?? "24h";

  try {
    if (range === "ytd") {
      const year = new Date().getUTCFullYear();
      const rows = await query(
        `SELECT timestamp, total_value_usd
         FROM portfolio_snapshots
         WHERE timestamp >= $1
         ORDER BY timestamp ASC`,
        [`${year}-01-01T00:00:00Z`],
      );
      return NextResponse.json({ data: toSnapshots(rows) });
    }

    const interval = RANGE_INTERVALS[range] ?? RANGE_INTERVALS["24h"];

    if (interval === null) {
      const rows = await query(
        `SELECT timestamp, total_value_usd
         FROM portfolio_snapshots
         ORDER BY timestamp ASC`,
      );
      return NextResponse.json({ data: toSnapshots(rows) });
    }

    const rows = await query(
      `SELECT timestamp, total_value_usd
       FROM portfolio_snapshots
       WHERE timestamp >= NOW() - $1::interval
       ORDER BY timestamp ASC`,
      [interval],
    );
    return NextResponse.json({ data: toSnapshots(rows) });
  } catch {
    return NextResponse.json({ error: "Database error" }, { status: 500 });
  }
}
