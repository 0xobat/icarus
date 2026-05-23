import { NextRequest, NextResponse } from "next/server";
import { getRedis } from "@/lib/server/redis";
import { randomUUID } from "crypto";

const VALID_BREAKERS = new Set([
  "drawdown",
  "gas_spike",
  "tx_failures",
  "position_loss",
  "tvl_monitor",
]);

/** POST /api/commands/breaker/reset — publishes breaker:reset to dashboard:commands stream. */
export async function POST(request: NextRequest) {
  const redis = getRedis();
  if (!redis) {
    return NextResponse.json({ error: "Redis unavailable" }, { status: 503 });
  }

  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const breakerName = body.breaker_name;
  if (typeof breakerName !== "string" || breakerName.trim().length === 0) {
    return NextResponse.json({ error: "Missing required field: breaker_name" }, { status: 400 });
  }

  if (!VALID_BREAKERS.has(breakerName.trim())) {
    return NextResponse.json(
      { error: `Invalid breaker_name. Must be one of: ${[...VALID_BREAKERS].join(", ")}` },
      { status: 400 },
    );
  }

  const commandId = randomUUID();
  const timestamp = new Date().toISOString();

  try {
    await redis.xadd(
      "dashboard:commands",
      "MAXLEN",
      "~",
      "1000",
      "*",
      "data",
      JSON.stringify({
        version: "1.0.0",
        command_id: commandId,
        timestamp,
        commandType: "breaker:reset",
        data: { breaker_name: breakerName.trim() },
      }),
    );
  } catch {
    return NextResponse.json({ error: "Failed to publish command" }, { status: 500 });
  }

  return NextResponse.json({ command_id: commandId });
}
