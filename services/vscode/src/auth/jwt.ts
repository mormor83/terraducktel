export interface AccessClaims { sub?: string; email?: string; role?: string; is_superadmin?: boolean; type?: string; exp?: number }
/** Decode (not verify) a JWT payload. Returns undefined for anything that isn't a 3-part token. */
export function decodeJwtPayload(token: string): AccessClaims | undefined {
  const parts = token.split(".");
  if (parts.length !== 3) return undefined;
  try {
    const json = Buffer.from(parts[1].replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
    const obj = JSON.parse(json);
    return typeof obj === "object" && obj ? (obj as AccessClaims) : undefined;
  } catch { return undefined; }
}
