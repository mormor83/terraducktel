import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { isTokenExpired, getValidToken, useCurrentUser, hasMinRole } from "./useAuth";
import { setToken } from "../api/client";

// Build a JWT-shaped string with the given payload (signature irrelevant).
function jwt(payload: Record<string, unknown>): string {
  const b64 = btoa(JSON.stringify(payload));
  return `h.${b64}.s`;
}

const FUTURE = Math.floor(Date.now() / 1000) + 3600;
const PAST = Math.floor(Date.now() / 1000) - 3600;

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("isTokenExpired", () => {
  it("true for null / unparseable / missing exp / past exp", () => {
    expect(isTokenExpired(null)).toBe(true);
    expect(isTokenExpired("not-a-jwt")).toBe(true);
    expect(isTokenExpired(jwt({}))).toBe(true);
    expect(isTokenExpired(jwt({ exp: PAST }))).toBe(true);
  });

  it("false for a future exp", () => {
    expect(isTokenExpired(jwt({ exp: FUTURE }))).toBe(false);
  });
});

describe("getValidToken", () => {
  it("returns null and clears storage for an expired token", () => {
    setToken(jwt({ exp: PAST }));
    expect(getValidToken()).toBeNull();
    expect(localStorage.getItem("terraducktel_token")).toBeNull();
  });

  it("returns the token when valid; null when absent", () => {
    expect(getValidToken()).toBeNull();
    const t = jwt({ exp: FUTURE });
    setToken(t);
    expect(getValidToken()).toBe(t);
  });
});

describe("useCurrentUser", () => {
  it("returns null without a valid token", () => {
    expect(useCurrentUser()).toBeNull();
  });

  it("maps claims, honoring explicit is_superadmin + name", () => {
    setToken(jwt({ exp: FUTURE, sub: "u1", email: "a@x.com", role: "operator", is_superadmin: true, name: " Alex " }));
    expect(useCurrentUser()).toEqual({
      id: "u1",
      email: "a@x.com",
      role: "operator",
      is_superadmin: true,
      name: "Alex",
    });
  });

  it("falls back is_superadmin to role==admin, role to viewer, name to null", () => {
    setToken(jwt({ exp: FUTURE, role: "admin" }));
    const u = useCurrentUser()!;
    expect(u.is_superadmin).toBe(true);
    expect(u.role).toBe("admin");
    expect(u.name).toBeNull();

    setToken(jwt({ exp: FUTURE }));
    expect(useCurrentUser()!.role).toBe("viewer");
    expect(useCurrentUser()!.is_superadmin).toBe(false);
  });
});

describe("hasMinRole", () => {
  const mk = (role: any) => ({ id: "", email: "", role, is_superadmin: false, name: null });
  it("respects the viewer<operator<admin hierarchy", () => {
    expect(hasMinRole(null, "viewer")).toBe(false);
    expect(hasMinRole(mk("viewer"), "viewer")).toBe(true);
    expect(hasMinRole(mk("viewer"), "operator")).toBe(false);
    expect(hasMinRole(mk("operator"), "operator")).toBe(true);
    expect(hasMinRole(mk("admin"), "operator")).toBe(true);
    expect(hasMinRole(mk("operator"), "admin")).toBe(false);
  });
});
