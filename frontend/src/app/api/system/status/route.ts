import { NextResponse } from "next/server";
import { getRedisValue } from "@/lib/server/redis";
import type { HoldModeData } from "@/lib/types";

export async function GET() {
  const data = await getRedisValue<HoldModeData>("dashboard:hold_mode");
  if (!data) {
    return NextResponse.json({ stale: true, data: null });
  }
  return NextResponse.json({ data });
}
