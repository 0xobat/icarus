import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { verifyJWT } from "@/lib/server/auth";
import { getRedis } from "@/lib/server/redis";

/**
 * Converts Redis stream field array [key, value, key, value, ...] to an object.
 */
function hashToObject(fields: string[]): Record<string, string> {
  const obj: Record<string, string> = {};
  for (let i = 0; i < fields.length; i += 2) {
    obj[fields[i]] = fields[i + 1];
  }
  return obj;
}

/**
 * SSE endpoint — subscribes to dashboard:events Redis stream via XREAD BLOCK,
 * forwards events as `data: {json}\n\n` messages.
 *
 * Defense-in-depth: verifies JWT auth within the route handler in addition
 * to middleware, since SSE connections are long-lived.
 */
export async function GET(request: NextRequest) {
  // Defense-in-depth: verify auth within route handler
  const cookieStore = await cookies();
  const token = cookieStore.get("icarus-session")?.value;
  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    await verifyJWT(token);
  } catch {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const redis = getRedis();
  if (!redis) {
    return new Response("Redis unavailable", { status: 503 });
  }

  // Use a SEPARATE Redis connection for blocking XREAD
  const subscriber = redis.duplicate();
  let cancelled = false;

  // Listen for client disconnect
  request.signal.addEventListener("abort", () => {
    cancelled = true;
    subscriber.disconnect();
  });

  const stream = new ReadableStream({
    async start(controller) {
      let lastId = "$"; // Only new messages
      const encoder = new TextEncoder();

      // Send initial keepalive
      controller.enqueue(encoder.encode(": keepalive\n\n"));

      try {
        while (!cancelled) {
          const results = await subscriber.xread(
            "BLOCK",
            "5000",
            "STREAMS",
            "dashboard:events",
            lastId,
          );

          if (cancelled) break;

          if (results) {
            for (const [, messages] of results) {
              for (const [id, fields] of messages) {
                lastId = id;
                const raw = hashToObject(fields);
                // Parse nested JSON in data field if present
                let parsed: Record<string, unknown> = raw;
                if (raw.data) {
                  try {
                    parsed = { ...raw, data: JSON.parse(raw.data) };
                  } catch {
                    // Keep as string if not valid JSON
                  }
                }
                controller.enqueue(
                  encoder.encode(`data: ${JSON.stringify(parsed)}\n\n`),
                );
              }
            }
          } else {
            // Send keepalive comment every 5s when no events
            controller.enqueue(encoder.encode(": keepalive\n\n"));
          }
        }
      } catch {
        // Client disconnected or error
      } finally {
        if (!cancelled) {
          subscriber.disconnect();
        }
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
