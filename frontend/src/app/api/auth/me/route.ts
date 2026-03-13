import { NextRequest, NextResponse } from "next/server";
import { verifyJWT } from "@/lib/server/auth";

export async function GET(request: NextRequest) {
  const token = request.cookies.get("icarus-session")?.value;

  if (!token) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  try {
    const payload = await verifyJWT(token);
    return NextResponse.json({ user: { username: payload.sub } });
  } catch {
    return NextResponse.json({ error: "Invalid session" }, { status: 401 });
  }
}
