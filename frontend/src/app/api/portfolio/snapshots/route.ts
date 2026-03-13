import { NextRequest, NextResponse } from "next/server";
import { query } from "@/lib/server/db";

const RANGE_INTERVALS: Record<string, string | null> = {
  "24h": "1 day",
  "7d": "7 days",
  "1m": "30 days",
  "3m": "90 days",
  all: null,
};

export async function GET(request: NextRequest) {
  const range = request.nextUrl.searchParams.get("range") ?? "24h";

  let interval: string | null;
  if (range === "ytd") {
    const year = new Date().getUTCFullYear();
    const rows = await query(
      `SELECT timestamp, total_value
       FROM portfolio_snapshots
       WHERE timestamp >= $1
       ORDER BY timestamp ASC`,
      [`${year}-01-01T00:00:00Z`],
    );

    const snapshots = rows.map((row) => ({
      timestamp: row.timestamp,
      total_value: parseFloat(row.total_value),
    }));

    return NextResponse.json({ data: snapshots });
  }

  interval = RANGE_INTERVALS[range] ?? RANGE_INTERVALS["24h"];

  if (interval === null) {
    // "all" — no time filter
    const rows = await query(
      `SELECT timestamp, total_value
       FROM portfolio_snapshots
       ORDER BY timestamp ASC`,
    );

    const snapshots = rows.map((row) => ({
      timestamp: row.timestamp,
      total_value: parseFloat(row.total_value),
    }));

    return NextResponse.json({ data: snapshots });
  }

  const rows = await query(
    `SELECT timestamp, total_value
     FROM portfolio_snapshots
     WHERE timestamp >= NOW() - $1::interval
     ORDER BY timestamp ASC`,
    [interval],
  );

  const snapshots = rows.map((row) => ({
    timestamp: row.timestamp,
    total_value: parseFloat(row.total_value),
  }));

  return NextResponse.json({ data: snapshots });
}
