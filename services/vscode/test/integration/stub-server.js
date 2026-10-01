// services/vscode/test/integration/stub-server.js — plain Node, no build step
const http = require("node:http");
const bus = [{ id: "bu1", slug: "alpha", name: "Alpha" }, { id: "bu2", slug: "beta", name: "Beta" }];
const mkWs = (id, bu, name) => ({ id, business_unit_id: bu, name, environment: "dev", aws_account_id: "123456789012", region: "eu-west-1", repo_url: "local://", tf_working_dir: `account-123456789012/eu-west-1/${name}`, repo_ref: "main", kind: "terraform", tags: {}, drift_status: "clean", path_status: "ok", state_backend: "s3" });
// Per-BU data: a request is only answered for a workspace/run when its X-Business-Unit header names the BU that owns it.
const wsByBu = { alpha: [mkWs("w1", "bu1", "vpc")], beta: [mkWs("w3", "bu2", "db")] };
let runs = [{ id: "r1", workspace_id: "w1", command: "plan", status: "planned", created_at: "2026-09-12T10:00:00Z" }];
const runsOf = (bu) => runs.filter((r) => wsByBu[bu]?.some((w) => w.id === r.workspace_id));
const steps = [{ id: "s1", run_id: "r2", position: 0, name: "Init", status: "success", output: "ok\n" }, { id: "s2", run_id: "r2", position: 1, name: "Plan", status: "success", output: "No changes.\n" }];
const json = (res, code, body) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(body)); };
http.createServer((req, res) => {
  const u = new URL(req.url, "http://x"); const p = u.pathname; const auth = req.headers.authorization;
  if (p === "/api/v1/auth/config") return json(res, 200, { mode: "local", oidc_enabled: false, cli_loopback: true });
  if (!auth || auth !== "Bearer tdt_smoke") return json(res, 401, { detail: "unauthenticated" });
  const bu = req.headers["x-business-unit"];
  if (p === "/api/v1/business-units") return json(res, 200, bus);
  if (p === "/api/v1/workspaces") return json(res, 200, wsByBu[bu] ?? []);
  if (p === "/api/v1/runs") return json(res, 200, runsOf(bu));
  if (p === "/api/v1/workspaces/w1/runs" && req.method === "POST") {
    if (bu !== "alpha") return json(res, 404, { detail: "workspace not in this business unit" }); const r = { id: "r2", workspace_id: "w1", command: "plan", status: "planned", created_at: "2026-09-12T11:00:00Z" }; runs = [r, ...runs]; return json(res, 201, r); }
  if (p === "/api/v1/runs/r2") return json(res, 200, runs[0]);
  if (p === "/api/v1/runs/r2/steps") return json(res, 200, steps);
  if (p === "/api/v1/runs/r2/plan") return json(res, 200, { plan_output: "No changes. Your infrastructure matches the configuration." });
  return json(res, 404, { detail: `stub: no route ${req.method} ${p}` });
}).listen(Number(process.env.STUB_PORT || 48765), "127.0.0.1");
