import * as http from "node:http";
import { AddressInfo } from "node:net";

export type Handler = (req: http.IncomingMessage, body: string, res: http.ServerResponse) => void;

/** In-process TDT stand-in. Register handlers by "METHOD /api/v1/path" (exact) or a RegExp. */
export class FakeServer {
  private server: http.Server;
  public calls: Array<{ method: string; url: string; headers: http.IncomingHttpHeaders; body: string }> = [];
  private routes: Array<{ m: string; p: string | RegExp; h: Handler }> = [];
  constructor() {
    this.server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        this.calls.push({ method: req.method!, url: req.url!, headers: req.headers, body });
        const path = req.url!.split("?")[0];
        const r = this.routes.find((x) => x.m === req.method && (typeof x.p === "string" ? x.p === path : x.p.test(path)));
        if (!r) { res.writeHead(404, { "content-type": "application/json" }); res.end(JSON.stringify({ detail: "Not Found" })); return; }
        r.h(req, body, res);
      });
    });
  }
  on(method: string, path: string | RegExp, h: Handler) { this.routes.push({ m: method, p: path, h }); return this; }
  json(method: string, path: string | RegExp, status: number, payload: unknown) {
    return this.on(method, path, (_q, _b, res) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(payload)); });
  }
  async start(): Promise<string> {
    await new Promise<void>((ok) => this.server.listen(0, "127.0.0.1", ok));
    const { port } = this.server.address() as AddressInfo;
    return `http://127.0.0.1:${port}`;
  }
  async stop() { await new Promise<void>((ok) => this.server.close(() => ok())); }
  requests(method: string, pathPrefix: string) { return this.calls.filter((c) => c.method === method && c.url.startsWith(pathPrefix)); }
}
