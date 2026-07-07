import { getToken, setToken } from "../api/client";

export type UserRole = "admin" | "operator" | "viewer";

export interface CurrentUser {
  id: string;
  email: string;
  role: UserRole;
  is_superadmin: boolean;
  /** Display name from the JWT `name` claim (OIDC users only). Null for
   *  local users — the UI prettifies the email local part in that case. */
  name: string | null;
}

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const base64 = token.split(".")[1];
    return JSON.parse(atob(base64));
  } catch {
    return null;
  }
}

/** True if the JWT's exp claim is in the past (or absent). */
export function isTokenExpired(token: string | null): boolean {
  if (!token) return true;
  const payload = parseJwtPayload(token);
  if (!payload) return true;
  const exp = typeof payload.exp === "number" ? payload.exp : 0;
  return exp * 1000 <= Date.now();
}

/** Get a valid token, or null. Side-effect: clears expired tokens from storage. */
export function getValidToken(): string | null {
  const t = getToken();
  if (!t) return null;
  if (isTokenExpired(t)) {
    setToken(null);
    return null;
  }
  return t;
}

export function useCurrentUser(): CurrentUser | null {
  const token = getValidToken();
  if (!token) return null;
  const payload = parseJwtPayload(token);
  if (!payload) return null;
  return {
    id: (payload.sub as string) ?? "",
    email: (payload.email as string) ?? "",
    role: (payload.role as UserRole) ?? "viewer",
    // Pre-migration tokens won't carry `is_superadmin` — fall back to role=admin
    // so the switcher still works for users who haven't re-logged-in yet.
    is_superadmin:
      typeof payload.is_superadmin === "boolean"
        ? payload.is_superadmin
        : payload.role === "admin",
    name: typeof payload.name === "string" && payload.name.trim() ? payload.name.trim() : null,
  };
}

const ROLE_LEVEL: Record<UserRole, number> = { viewer: 0, operator: 1, admin: 2 };

export function hasMinRole(user: CurrentUser | null, minimum: UserRole): boolean {
  if (!user) return false;
  return ROLE_LEVEL[user.role] >= ROLE_LEVEL[minimum];
}
