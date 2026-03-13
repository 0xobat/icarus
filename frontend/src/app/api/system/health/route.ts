import { NextResponse } from "next/server";
import { getRedisValue } from "@/lib/server/redis";
import type { ServiceHealth } from "@/lib/types";

export async function GET() {
  const data = await getRedisValue<ServiceHealth[]>("dashboard:health");
  if (!data) {
    return NextResponse.json({ stale: true, data: null });
  }
  return NextResponse.json({ data });
}
