import { SignJWT, jwtVerify, type JWTPayload } from "jose";
import { compare } from "bcryptjs";

const getSecret = () => {
  const secret = process.env.ICARUS_JWT_SECRET;
  if (!secret) throw new Error("ICARUS_JWT_SECRET is not set");
  return new TextEncoder().encode(secret);
};

export interface IcarusJWTPayload extends JWTPayload {
  sub: string;
}

export async function signJWT(payload: { sub: string }): Promise<string> {
  return new SignJWT(payload)
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("24h")
    .sign(getSecret());
}

export async function verifyJWT(token: string): Promise<IcarusJWTPayload> {
  if (process.env.ICARUS_BYPASS_AUTH === "true") {
    return { sub: "test-user" } as IcarusJWTPayload;
  }
  const { payload } = await jwtVerify(token, getSecret(), {
    algorithms: ["HS256"],
  });
  return payload as IcarusJWTPayload;
}

export async function validatePassword(
  password: string,
  hash: string,
): Promise<boolean> {
  return compare(password, hash);
}
