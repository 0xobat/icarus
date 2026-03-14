import { NextRequest, NextResponse } from "next/server";
import { getRedis } from "@/lib/server/redis";
import { randomUUID } from "crypto";

/** POST /api/commands/strategy/deactivate — publishes strategy:deactivate to dashboard:commands stream. */
export async function POST(request: NextRequest) {
  const redis = getRedis();
  if (!redis) {
    return NextResponse.json({ error: "Redis unavailable" }, { status: 503 });
  }

  const body = await request.json();
  const commandId = randomUUID();
  const timestamp = new Date().toISOString();

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
      commandType: "strategy:deactivate",
      data: { strategy_id: body.strategy_id },
    }),
  );

  return NextResponse.json({ command_id: commandId });
}
