import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import { MemorySecretStore } from "../../src/auth/secrets";
import { TokenManager } from "../../src/auth/tokenManager";

const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
const jwt = (p: object) => `${b64({ alg: "none" })}.${b64(p)}.x`;

describe("TokenManager", () => {
  let srv: FakeServer; let url: string; let store: MemorySecretStore; let tm: TokenManager; let client: TdtClient;
  beforeEach(async () => {
    srv = new FakeServer(); url = await srv.start(); store = new MemorySecretStore();
    tm = new TokenManager(store, "prod");
    client = new TdtClient({ baseUrl: url, bu: "default", tokens: tm });
    tm.attach(client);
  });
  afterEach(async () => { await srv.stop(); });

  it("password sign-in stores only the refresh token, keeps access in memory", async () => {
    srv.json("POST", "/api/v1/auth/token", 200, { access_token: jwt({ email: "a@b", role: "admin", type: "access" }), refresh_token: jwt({ type: "refresh" }) });
    await tm.signInWithPassword("a@b", "pw");
    expect(await tm.getAccessToken()).toContain(".");
    const raw = JSON.parse((await store.get("terraducktel.cred.prod"))!);
    expect(raw.kind).toBe("password"); expect(raw.refresh_token).toBeDefined(); expect(raw.access_token).toBeUndefined();
    expect(tm.claims()).toMatchObject({ email: "a@b", role: "admin" }); expect(tm.isSignedIn()).toBe(true);
  });

  it("refreshAccessToken redeems the refresh token and rotates it", async () => {
    await store.store("terraducktel.cred.prod", JSON.stringify({ kind: "password", refresh_token: "r1" }));
    srv.json("POST", "/api/v1/auth/refresh", 200, { access_token: jwt({ role: "viewer" }), refresh_token: "r2" });
    const acc = await tm.refreshAccessToken();
    expect(acc).toContain("."); expect(JSON.parse(srv.calls[0].body)).toEqual({ refresh_token: "r1" });
    expect(JSON.parse((await store.get("terraducktel.cred.prod"))!).refresh_token).toBe("r2");
  });

  it("API key is returned as the access token and never refreshed", async () => {
    await tm.signInWithApiKey("tdt_secret");
    expect(await tm.getAccessToken()).toBe("tdt_secret");
    expect(await tm.refreshAccessToken()).toBe("tdt_secret");
    expect(tm.kind()).toBe("api_key"); expect(tm.claims()).toBeUndefined();
  });

  it("refresh failure returns undefined and signOut wipes the secret", async () => {
    await store.store("terraducktel.cred.prod", JSON.stringify({ kind: "password", refresh_token: "dead" }));
    srv.json("POST", "/api/v1/auth/refresh", 401, { detail: "invalid" });
    expect(await tm.refreshAccessToken()).toBeUndefined();
    await tm.signOut();
    expect(await store.get("terraducktel.cred.prod")).toBeUndefined(); expect(tm.isSignedIn()).toBe(false);
  });

  it("coalesces concurrent lazy refreshes into a single POST /auth/refresh", async () => {
    // Refresh tokens rotate: two parallel redemptions of the same one would leave the loser
    // holding a token the server already invalidated.
    await store.store("terraducktel.cred.prod", JSON.stringify({ kind: "password", refresh_token: "r1" }));
    srv.on("POST", "/api/v1/auth/refresh", (_q, _b, res) => {
      setTimeout(() => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify({ access_token: jwt({ role: "viewer" }), refresh_token: "r2" })); }, 25);
    });
    const [a, b] = await Promise.all([tm.getAccessToken(), tm.getAccessToken()]);
    expect(a).toBeDefined(); expect(a).toBe(b);
    expect(srv.requests("POST", "/api/v1/auth/refresh").length).toBe(1);
  });

  it("a transient (5xx) refresh failure fails the request but keeps the credential", async () => {
    await tm.signInWithTokenPair({ access_token: jwt({ role: "admin" }), refresh_token: "r1" }, "password");
    srv.json("GET", "/api/v1/workspaces", 401, { detail: "expired" });
    srv.json("POST", "/api/v1/auth/refresh", 502, { detail: "bad gateway" });
    let signedOut = 0; client.onSignedOut(() => signedOut++);
    await expect(client.listWorkspaces()).rejects.toMatchObject({ status: 502 });
    expect(signedOut).toBe(0);
    expect(tm.isSignedIn()).toBe(true);
    expect(JSON.parse((await store.get("terraducktel.cred.prod"))!).refresh_token).toBe("r1");
  });

  it("a 4xx refresh failure takes the sign-out path", async () => {
    await tm.signInWithTokenPair({ access_token: jwt({ role: "admin" }), refresh_token: "r1" }, "password");
    srv.json("GET", "/api/v1/workspaces", 401, { detail: "expired" });
    srv.json("POST", "/api/v1/auth/refresh", 401, { detail: "invalid refresh token" });
    let signedOut = 0; client.onSignedOut(() => signedOut++);
    await expect(client.listWorkspaces()).rejects.toMatchObject({ status: 401 });
    expect(signedOut).toBe(1);
    expect(tm.isSignedIn()).toBe(false);
    expect(await store.get("terraducktel.cred.prod")).toBeUndefined();
  });

  it("restores a stored credential on construction", async () => {
    await store.store("terraducktel.cred.prod", JSON.stringify({ kind: "api_key", api_key: "tdt_k" }));
    const tm2 = new TokenManager(store, "prod"); await tm2.restore();
    expect(tm2.isSignedIn()).toBe(true); expect(await tm2.getAccessToken()).toBe("tdt_k");
  });

  it("concurrent first getAccessToken() calls (no explicit restore()) share one secret-store read", async () => {
    let getCalls = 0;
    class DelayedStore extends MemorySecretStore {
      async get(k: string) {
        getCalls++;
        await new Promise((r) => setTimeout(r, 20));
        return super.get(k);
      }
    }
    const delayed = new DelayedStore();
    await delayed.store("terraducktel.cred.prod", JSON.stringify({ kind: "api_key", api_key: "tdt_k" }));
    const tm3 = new TokenManager(delayed, "prod");
    const [a, b] = await Promise.all([tm3.getAccessToken(), tm3.getAccessToken()]);
    expect(a).toBe("tdt_k"); expect(b).toBe("tdt_k");
    expect(getCalls).toBe(1);
  });
});
