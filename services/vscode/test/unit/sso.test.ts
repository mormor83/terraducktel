import { describe, expect, it } from "vitest";
import * as http from "node:http";
import { runLoopbackLogin } from "../../src/auth/sso";

function hit(url: string) { return new Promise<number>((ok) => http.get(url, (r) => { r.resume(); ok(r.statusCode ?? 0); })); }

describe("runLoopbackLogin", () => {
  it("resolves with the token pair when the callback carries the right nonce", async () => {
    let captured!: { port: number; nonce: string };
    const p = runLoopbackLogin({ buildUrl: (port, nonce) => { captured = { port, nonce }; return `http://x/login?cli_port=${port}&cli_nonce=${nonce}`; }, openUrl: async () => true, timeoutMs: 5000 });
    await new Promise((r) => setTimeout(r, 20));
    const status = await hit(`http://127.0.0.1:${captured.port}/callback?access_token=A&refresh_token=R&nonce=${captured.nonce}`);
    expect(status).toBe(200);
    await expect(p).resolves.toEqual({ access_token: "A", refresh_token: "R" });
  });

  it("rejects a callback with a wrong nonce and keeps waiting, then times out", async () => {
    let captured!: { port: number; nonce: string };
    const p = runLoopbackLogin({ buildUrl: (port, nonce) => { captured = { port, nonce }; return "http://x"; }, openUrl: async () => true, timeoutMs: 300 });
    await new Promise((r) => setTimeout(r, 20));
    expect(await hit(`http://127.0.0.1:${captured.port}/callback?access_token=A&refresh_token=R&nonce=WRONG`)).toBe(400);
    await expect(p).rejects.toThrow(/timed out/);
  });

  it("generates a 32+ char url-safe nonce and passes the bound port", async () => {
    let captured!: { port: number; nonce: string };
    const p = runLoopbackLogin({ buildUrl: (port, nonce) => { captured = { port, nonce }; return "http://x"; }, openUrl: async () => true, timeoutMs: 200 });
    await new Promise((r) => setTimeout(r, 20));
    expect(captured.port).toBeGreaterThan(0); expect(captured.nonce).toMatch(/^[A-Za-z0-9_-]{32,}$/);
    await p.catch(() => undefined);
  });
});
