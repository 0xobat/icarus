import { NextRequest, NextResponse } from "next/server";
import { getRedis } from "@/lib/server/redis";
import { randomUUID } from "crypto";

/** POST /api/commands/hold/enter — publishes system:enter_hold to dashboard:commands stream. */
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

  const reason = body.reason;
  if (typeof reason !== "string" || reason.trim().length === 0) {
    return NextResponse.json({ error: "Missing required field: reason" }, { status: 400 });
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
        commandType: "system:enter_hold",
        data: { reason: reason.trim() },
      }),
    );
  } catch {
    return NextResponse.json({ error: "Failed to publish command" }, { status: 500 });
  }

  return NextResponse.json({ command_id: commandId });
}
