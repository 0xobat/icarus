import { NextResponse } from "next/server";
import { getRedisValue } from "@/lib/server/redis";
import type { MetricsData } from "@/lib/types";

export async function GET() {
  const data = await getRedisValue<MetricsData>("dashboard:metrics");
  if (!data) {
    return NextResponse.json({ stale: true, data: null });
  }
  return NextResponse.json({ data });
}
