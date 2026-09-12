import * as http from "node:http";
import { randomBytes } from "node:crypto";
import { AddressInfo } from "node:net";
import { URL } from "node:url";
import type { TokenPair } from "../api/types";

export interface LoopbackOptions {
  /** Build the browser URL for a given bound port + nonce (client.ssoLoginUrl). */
  buildUrl: (port: number, nonce: string) => string;
  openUrl: (url: string) => Promise<boolean>;
  timeoutMs?: number;
  /** Called once, synchronously, with a function that aborts the login: the loopback server is
   *  closed (freeing the port) and the promise rejects with "SSO sign-in cancelled". Wired to
   *  the progress notification's cancel button, and to a second `signIn()` superseding this one
   *  — without it an abandoned sign-in would sit on its port for the full five-minute timeout. */
  onCancel?: (cancel: () => void) => void;
}

const DONE_HTML = `<!doctype html><meta charset="utf-8"><title>Terraducktel</title><body style="font:14px system-ui;padding:3rem;text-align:center"><p>Signed in to Terraducktel for VS Code.</p><p style="color:#666">You can close this tab.</p></body>`;

/** Mirror of the tdt CLI's browser sign-in: listen on 127.0.0.1:<random>, send the browser
 *  to the API's /auth/oidc/login with cli_port+cli_nonce, and wait for the server's page to
 *  redirect back to /callback?access_token&refresh_token&nonce. The nonce must match. */
export function runLoopbackLogin(opts: LoopbackOptions): Promise<TokenPair> {
  const nonce = randomBytes(24).toString("base64url"); // 32 url-safe chars
  const timeoutMs = opts.timeoutMs ?? 5 * 60_000;
  return new Promise<TokenPair>((resolve, reject) => {
    let settled = false;
    const server = http.createServer((req, res) => {
      const u = new URL(req.url ?? "/", "http://127.0.0.1");
      if (u.pathname !== "/callback") { res.writeHead(404); res.end(); return; }
      const access = u.searchParams.get("access_token"), refresh = u.searchParams.get("refresh_token"), got = u.searchParams.get("nonce");
      // Two very different failures, and telling them apart is the whole diagnosis: a nonce
      // mismatch means someone else's callback reached this listener, missing tokens mean the
      // server's redirect itself was malformed.
      if (got !== nonce) { res.writeHead(400, { "content-type": "text/plain" }); res.end("Bad sign-in callback (nonce mismatch). Try signing in again."); return; }
      if (!access || !refresh) { res.writeHead(400, { "content-type": "text/plain" }); res.end("Bad sign-in callback (missing tokens). Try signing in again."); return; }
      res.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" }); res.end(DONE_HTML);
      finish(() => resolve({ access_token: access, refresh_token: refresh }));
    });
    const timer = setTimeout(() => finish(() => reject(new Error("SSO sign-in timed out waiting for the browser callback"))), timeoutMs);
    function finish(cb: () => void) { if (settled) return; settled = true; clearTimeout(timer); server.close(); cb(); }
    server.on("error", (e) => finish(() => reject(e)));
    opts.onCancel?.(() => finish(() => reject(new Error("SSO sign-in cancelled"))));
    server.listen(0, "127.0.0.1", () => {
      if (settled) { server.close(); return; }   // cancelled before we finished binding
      const { port } = server.address() as AddressInfo;
      opts.openUrl(opts.buildUrl(port, nonce)).then((ok) => { if (!ok) finish(() => reject(new Error("Could not open the browser for SSO sign-in"))); }, (e) => finish(() => reject(e)));
    });
  });
}
