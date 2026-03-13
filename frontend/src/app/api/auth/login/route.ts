import { NextRequest, NextResponse } from "next/server";
import { signJWT, validatePassword } from "@/lib/server/auth";

export async function POST(request: NextRequest) {
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
      return NextResponse.json(
        { error: "Invalid credentials" },
        { status: 401 },
      );
    }

    const valid = await validatePassword(password, adminHash);
    if (!valid) {
      return NextResponse.json(
        { error: "Invalid credentials" },
        { status: 401 },
      );
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
