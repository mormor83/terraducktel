import { describe, expect, it } from "vitest";
import * as http from "node:http";
import { runLoopbackLogin } from "../../src/auth/sso";

function hit(url: string) { return new Promise<number>((ok) => http.get(url, (r) => { r.resume(); ok(r.statusCode ?? 0); })); }

/** True once nothing is listening on `port` any more — i.e. the loopback server really closed. */
async function portReleased(port: number): Promise<boolean> {
  for (let i = 0; i < 30; i++) {
    const free = await new Promise<boolean>((ok) => {
      const s = http.createServer();
      s.once("error", () => ok(false));
      s.listen(port, "127.0.0.1", () => s.close(() => ok(true)));
    });
    if (free) return true;
    await new Promise((r) => setTimeout(r, 10));
  }
  return false;
}

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

  it("rejects a callback that is missing tokens with a distinct message", async () => {
    let captured!: { port: number; nonce: string };
    const p = runLoopbackLogin({ buildUrl: (port, nonce) => { captured = { port, nonce }; return "http://x"; }, openUrl: async () => true, timeoutMs: 300 });
    await new Promise((r) => setTimeout(r, 20));
    expect(await hit(`http://127.0.0.1:${captured.port}/callback?nonce=${captured.nonce}`)).toBe(400);
    await expect(p).rejects.toThrow(/timed out/);
  });

  it("cancelling rejects and releases the loopback port", async () => {
    let captured!: { port: number };
    let cancel!: () => void;
    const p = runLoopbackLogin({
      buildUrl: (port) => { captured = { port }; return "http://x"; },
      openUrl: async () => true,
      timeoutMs: 60_000,                      // would hang the suite if cancel did not work
      onCancel: (c) => (cancel = c),
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(captured.port).toBeGreaterThan(0);
    cancel();
    await expect(p).rejects.toThrow(/cancelled/i);
    expect(await portReleased(captured.port)).toBe(true);
  });

  it("generates a 32+ char url-safe nonce and passes the bound port", async () => {
    let captured!: { port: number; nonce: string };
    const p = runLoopbackLogin({ buildUrl: (port, nonce) => { captured = { port, nonce }; return "http://x"; }, openUrl: async () => true, timeoutMs: 200 });
    await new Promise((r) => setTimeout(r, 20));
    expect(captured.port).toBeGreaterThan(0); expect(captured.nonce).toMatch(/^[A-Za-z0-9_-]{32,}$/);
    await p.catch(() => undefined);
  });
});
