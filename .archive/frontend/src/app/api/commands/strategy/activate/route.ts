import { NextRequest, NextResponse } from "next/server";
import { getRedis } from "@/lib/server/redis";
import { randomUUID } from "crypto";

/** POST /api/commands/strategy/activate — publishes strategy:activate to dashboard:commands stream. */
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

  const strategyId = body.strategy_id;
  if (typeof strategyId !== "string" || strategyId.trim().length === 0) {
    return NextResponse.json({ error: "Missing required field: strategy_id" }, { status: 400 });
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
        commandType: "strategy:activate",
        data: { strategy_id: strategyId.trim() },
      }),
    );
  } catch {
    return NextResponse.json({ error: "Failed to publish command" }, { status: 500 });
  }

  return NextResponse.json({ command_id: commandId });
}
