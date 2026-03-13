import { NextResponse } from "next/server";
import { getRedisValue } from "@/lib/server/redis";
import type { CircuitBreaker } from "@/lib/types";

export async function GET() {
  const data = await getRedisValue<CircuitBreaker[]>("dashboard:breakers");
  if (!data) {
    return NextResponse.json({ stale: true, data: null });
  }
  return NextResponse.json({ data });
}
