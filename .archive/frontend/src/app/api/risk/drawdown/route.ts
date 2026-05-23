import { NextResponse } from "next/server";
import { getRedisValue } from "@/lib/server/redis";

export async function GET() {
  const data = await getRedisValue("dashboard:drawdown");
  if (!data) {
    return NextResponse.json({ stale: true, data: null });
  }
  return NextResponse.json({ data });
}
