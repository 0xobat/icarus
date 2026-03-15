import { NextRequest, NextResponse } from "next/server";
import { signJWT, validatePassword } from "@/lib/server/auth";
import { getRedis } from "@/lib/server/redis";

const MAX_ATTEMPTS = 5;
const WINDOW_SECONDS = 60;

async function checkRateLimit(ip: string): Promise<{ allowed: boolean; remaining: number }> {
  const redis = getRedis();
  if (!redis) return { allowed: true, remaining: MAX_ATTEMPTS };

  const key = `ratelimit:login:${ip}`;
  const count = await redis.incr(key);

  // Set TTL on first increment only
  if (count === 1) {
    await redis.expire(key, WINDOW_SECONDS);
  }

  return { allowed: count <= MAX_ATTEMPTS, remaining: Math.max(0, MAX_ATTEMPTS - count) };
}

export async function POST(request: NextRequest) {
  const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim()
    ?? request.headers.get("x-real-ip")
    ?? "unknown";

  const { allowed, remaining } = await checkRateLimit(ip);
  if (!allowed) {
    const response = NextResponse.json(
      { error: "Too many login attempts. Try again later." },
      { status: 429 },
    );
    response.headers.set("Retry-After", String(WINDOW_SECONDS));
    response.headers.set("X-RateLimit-Remaining", "0");
    return response;
  }

  try {
    const { username, password } = await request.json();

    const adminUser = process.env.ICARUS_ADMIN_USER;
    const adminHash = process.env.ICARUS_ADMIN_PASSWORD_HASH;

    if (!adminUser || !adminHash) {
      return NextResponse.json(
        { error: "Auth not configured" },
        { status: 500 },
      );
    }

    if (username !== adminUser) {
      const res = NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
      res.headers.set("X-RateLimit-Remaining", String(remaining));
      return res;
    }

    const valid = await validatePassword(password, adminHash);
    if (!valid) {
      const res = NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
      res.headers.set("X-RateLimit-Remaining", String(remaining));
      return res;
    }

    const token = await signJWT({ sub: username });

    const response = NextResponse.json({ success: true });
    response.cookies.set("icarus-session", token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      maxAge: 60 * 60 * 24, // 24h
      path: "/",
    });

    return response;
  } catch {
    return NextResponse.json(
      { error: "Invalid credentials" },
      { status: 401 },
    );
  }
}
