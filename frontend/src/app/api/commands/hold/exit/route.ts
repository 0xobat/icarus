import { NextResponse } from "next/server";
import { getRedis } from "@/lib/server/redis";
import { randomUUID } from "crypto";

/** POST /api/commands/hold/exit — publishes system:exit_hold to dashboard:commands stream. */
export async function POST() {
  const redis = getRedis();
  if (!redis) {
    return NextResponse.json({ error: "Redis unavailable" }, { status: 503 });
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
        commandType: "system:exit_hold",
        data: {},
      }),
    );
  } catch {
    return NextResponse.json({ error: "Failed to publish command" }, { status: 500 });
  }

  return NextResponse.json({ command_id: commandId });
}
