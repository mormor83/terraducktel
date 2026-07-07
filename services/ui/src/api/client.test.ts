import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getToken,
  setToken,
  getSavedCredentials,
  setSavedCredentials,
  getCurrentBusinessUnit,
  setCurrentBusinessUnit,
  createApiClient,
} from "./client";

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("token storage", () => {
  it("sets, gets, and clears the token", () => {
    expect(getToken()).toBeNull();
    setToken("abc");
    expect(getToken()).toBe("abc");
    setToken(null);
    expect(getToken()).toBeNull();
  });
});

describe("saved credentials (email-only + legacy purge)", () => {
  it("returns null when not opted in", () => {
    expect(getSavedCredentials()).toBeNull();
  });

  it("round-trips email and clears it", () => {
    setSavedCredentials({ email: "me@x.com" });
    expect(getSavedCredentials()).toEqual({ email: "me@x.com" });
    setSavedCredentials(null);
    expect(getSavedCredentials()).toBeNull();
  });

  it("returns null when remembered but email missing", () => {
    localStorage.setItem("terraducktel_remember", "1");
    expect(getSavedCredentials()).toBeNull();
  });

  it("purges a leftover legacy cleartext password", () => {
    localStorage.setItem("terraducktel_saved_password", "hunter2");
    getSavedCredentials();
    expect(localStorage.getItem("terraducktel_saved_password")).toBeNull();
  });
});

describe("business unit selection", () => {
  it("get/set and fires a change event", () => {
    const spy = vi.fn();
    window.addEventListener("terraducktel:bu-changed", spy);
    expect(getCurrentBusinessUnit()).toBeNull();
    setCurrentBusinessUnit("acme");
    expect(getCurrentBusinessUnit()).toBe("acme");
    setCurrentBusinessUnit(null);
    expect(getCurrentBusinessUnit()).toBeNull();
    expect(spy).toHaveBeenCalledTimes(2);
    window.removeEventListener("terraducktel:bu-changed", spy);
  });
});

describe("request interceptor", () => {
  function runInterceptor(headers: Record<string, unknown> = {}) {
    const c = createApiClient();
    const handler = (c.interceptors.request as any).handlers[0].fulfilled;
    return handler({ headers });
  }

  it("injects the bearer token when present", () => {
    setToken("tok");
    expect(runInterceptor().headers.Authorization).toBe("Bearer tok");
  });

  it("omits Authorization when no token", () => {
    expect(runInterceptor().headers.Authorization).toBeUndefined();
  });

  it("adds the current BU slug header", () => {
    setCurrentBusinessUnit("acme");
    expect(runInterceptor().headers["X-Business-Unit"]).toBe("acme");
  });

  it("maps empty-string BU ('all BUs') to the 'all' header", () => {
    setCurrentBusinessUnit("");
    expect(runInterceptor().headers["X-Business-Unit"]).toBe("all");
  });

  it("never sets the BU header when unset", () => {
    expect(runInterceptor().headers["X-Business-Unit"]).toBeUndefined();
  });

  it("does not clobber an explicit per-call BU header", () => {
    setCurrentBusinessUnit("acme");
    const out = runInterceptor({ "X-Business-Unit": "" });
    expect(out.headers["X-Business-Unit"]).toBe("");
  });
});
