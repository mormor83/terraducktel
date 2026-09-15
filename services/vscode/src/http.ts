import * as http from "node:http";
import * as https from "node:https";
import { URL } from "node:url";

export interface HttpRequest {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  url: string;
  headers?: Record<string, string>;
  body?: unknown;            // JSON-encoded when defined
  timeoutMs?: number;        // default 30 000
  insecureTls?: boolean;     // https only: skip certificate verification
}
export interface HttpResponse { status: number; headers: http.IncomingHttpHeaders; text: string }

/** Minimal JSON-over-HTTP using Node's built-ins, so the bundle has no runtime deps
 *  and per-profile insecure TLS never touches process-global settings. */
export function request(opts: HttpRequest): Promise<HttpResponse> {
  const u = new URL(opts.url);
  const isHttps = u.protocol === "https:";
  const payload = opts.body === undefined ? undefined : Buffer.from(JSON.stringify(opts.body), "utf8");
  const headers: Record<string, string> = { accept: "application/json", ...(opts.headers ?? {}) };
  if (payload) { headers["content-type"] = "application/json"; headers["content-length"] = String(payload.length); }
  const reqOpts: https.RequestOptions = {
    method: opts.method, hostname: u.hostname, port: u.port || (isHttps ? 443 : 80),
    path: u.pathname + u.search, headers, timeout: opts.timeoutMs ?? 30_000,
    ...(isHttps && opts.insecureTls ? { rejectUnauthorized: false } : {}),
  };
  return new Promise((resolve, reject) => {
    const req = (isHttps ? https : http).request(reqOpts, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (c: Buffer) => chunks.push(c));
      res.on("end", () => resolve({ status: res.statusCode ?? 0, headers: res.headers, text: Buffer.concat(chunks).toString("utf8") }));
    });
    req.on("timeout", () => req.destroy(new Error(`request to ${u.host} timed out`)));
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}
