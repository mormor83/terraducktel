# VS Code Extension — Milestone C Implementation Plan (approval notifications)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While signed in, the extension polls for runs awaiting approval in the active business unit (all BUs for a superadmin on `all`), deduplicates per run, and surfaces each new one as a VS Code notification with **Approve…**, **Reject…** and **Open** actions. Approve always goes through the existing confirmation modal.

**Architecture:** A pure `ApprovalWatcher` (injected client getter, clock, notifier, persistence) owns the poll loop, the seen-set and the 24 h persisted memory; `extension.ts` wires it to the `Session`, VS Code notifications and `globalState`. Polling is independent of sidebar visibility (that is the point of notifications) but stops while signed out and when `terraducktel.approvals.pollSeconds` is 0. Reuses `terraducktel.approve` / `terraducktel.reject` / `terraducktel.openInBrowser` with a `RunNode` argument.

**Tech Stack:** as milestones A/B.

**Spec:** `docs/superpowers/specs/2026-09-12-vscode-extension-design.md` §6

## Global Constraints

- Same as A/B (no runtime deps; secrets never in notifications; commit trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` then `Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit`; commands from `services/vscode` in the `vscode-extension` worktree).
- Poll: `GET /runs?status=awaiting_approval&limit=100` on the active BU client (already carries `X-Business-Unit`; superadmin "all" is just the `all` slug). Interval `terraducktel.approvals.pollSeconds` (number, default 60, minimum 15 unless 0; 0 disables). One in-flight poll at a time; failures are silent (logged at trace), never toast.
- Dedupe: a run notifies at most once per 24 h across window reloads (`globalState` key `terraducktel.approvals.seen` → `{ [runId]: epochMs }`, pruned on each save). On first activation (or after sign-in) the CURRENT awaiting set is recorded as seen **without** notifying, so a fresh install doesn't spray a backlog — the Runs-view badge already shows the count.
- Notification text: `TDT: <workspace name> <command> is awaiting approval (<+add ~change -destroy>)` when the graph summary is cheap to fetch (one `GET /runs/{id}/graph`, failures → text without the summary). Actions: **Approve…** → `terraducktel.approve` with a `RunNode`; **Reject…** → `terraducktel.reject`; **Open** → `terraducktel.openInBrowser` with the `RunNode`.
- Version bump `0.2.0` → `0.3.0`.

## File structure

| File | Responsibility |
|---|---|
| `src/notifications/approvals.ts` | `ApprovalWatcher` (pure loop + dedupe + persistence via injected `Memento`-like store) |
| `test/unit/approvals.test.ts` | fake server + fake clock/notifier tests |
| `src/extension.ts` | wiring, settings reaction |
| `package.json` | setting, version |
| `docs/VSCODE.md`, spec | docs |

---

### Task 1: `ApprovalWatcher`

**Files:**
- Create: `services/vscode/src/notifications/approvals.ts`, `services/vscode/test/unit/approvals.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface SeenStore { get(): Record<string, number> | undefined; set(v: Record<string, number>): Promise<void> }
  export interface ApprovalNotice { run: Run; workspaceName: string; summary?: GraphSummary }
  export class ApprovalWatcher {
    constructor(deps: { client: () => TdtClient | undefined; workspaceName: (id: string) => string; notify: (n: ApprovalNotice) => void; seen: SeenStore; now?: () => number; trace?: (l: string) => void; ttlMs?: number });
    start(intervalMs: number): void;   // (re)arms the timer; 0 → stop()
    stop(): void;
    poll(): Promise<void>;             // one pass; safe to call concurrently (single-flight)
    prime(): Promise<void>;            // record current awaiting runs as seen without notifying
    dispose(): void;
  }
  ```

- [ ] **Step 1: Failing tests**

```ts
// services/vscode/test/unit/approvals.test.ts
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import { ApprovalWatcher, type ApprovalNotice, type SeenStore } from "../../src/notifications/approvals";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", signOut: async () => {}, hasCredential: () => true };
const run = (id: string, ws = "w1") => ({ id, workspace_id: ws, command: "apply", status: "awaiting_approval", created_at: "2026-09-12T10:00:00Z" });
function memStore(initial?: Record<string, number>): SeenStore & { value?: Record<string, number> } {
  const s: SeenStore & { value?: Record<string, number> } = { value: initial, get: () => s.value, set: async (v) => { s.value = v; } };
  return s;
}

describe("ApprovalWatcher", () => {
  let srv: FakeServer; let url: string; let client: TdtClient; let notices: ApprovalNotice[]; let now: number;
  beforeEach(async () => { srv = new FakeServer(); url = await srv.start(); client = new TdtClient({ baseUrl: url, bu: "default", tokens }); notices = []; now = 1_000_000; });
  afterEach(async () => { await srv.stop(); });
  const mk = (seen = memStore(), ttlMs?: number) => new ApprovalWatcher({ client: () => client, workspaceName: (id) => (id === "w1" ? "vpc" : id), notify: (n) => notices.push(n), seen, now: () => now, ttlMs });

  it("notifies once per new awaiting run, with the graph summary when available", async () => {
    let awaiting = [run("r1")];
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(awaiting)); });
    srv.json("GET", "/api/v1/runs/r1/graph", 200, { nodes: [], edges: [], summary: { add: 1, change: 2, destroy: 0 } });
    const w = mk();
    await w.poll(); await w.poll();
    expect(notices.map((n) => n.run.id)).toEqual(["r1"]);
    expect(notices[0]).toMatchObject({ workspaceName: "vpc", summary: { add: 1, change: 2, destroy: 0 } });
    awaiting = [run("r1"), run("r2", "w2")];
    srv.json("GET", "/api/v1/runs/r2/graph", 500, { detail: "boom" });
    await w.poll();
    expect(notices.map((n) => n.run.id)).toEqual(["r1", "r2"]);
    expect(notices[1].summary).toBeUndefined();
    const q = new URL(srv.requests("GET", "/api/v1/runs")[0].url, "http://x").searchParams;
    expect(q.get("status")).toBe("awaiting_approval"); expect(q.get("limit")).toBe("100");
  });

  it("prime() records the current set as seen without notifying", async () => {
    srv.json("GET", "/api/v1/runs", 200, [run("r1"), run("r2")]);
    const seen = memStore(); const w = mk(seen);
    await w.prime(); await w.poll();
    expect(notices).toEqual([]); expect(Object.keys(seen.value ?? {}).sort()).toEqual(["r1", "r2"]);
  });

  it("persists seen ids with timestamps, prunes entries older than the TTL, and honours persisted state across instances", async () => {
    srv.json("GET", "/api/v1/runs", 200, [run("r1")]); srv.json("GET", "/api/v1/runs/r1/graph", 200, { nodes: [], edges: [], summary: {} });
    const seen = memStore({ old: now - 25 * 3_600_000, fresh: now - 3_600_000 });
    await mk(seen, 24 * 3_600_000).poll();
    expect(seen.value).toMatchObject({ r1: now, fresh: now - 3_600_000 }); expect(seen.value?.old).toBeUndefined();
    notices = [];
    await mk(seen).poll();                    // a new instance (window reload) with the same store
    expect(notices).toEqual([]);
  });

  it("is single-flight and silent on failures", async () => {
    let inflight = 0, max = 0;
    srv.on("GET", "/api/v1/runs", (_q, _b, res) => { inflight++; max = Math.max(max, inflight); setTimeout(() => { inflight--; res.writeHead(503, { "content-type": "application/json" }); res.end("{}"); }, 30); });
    const w = mk();
    await Promise.all([w.poll(), w.poll(), w.poll()]);
    expect(max).toBe(1); expect(notices).toEqual([]);
  });

  it("does nothing without a client, and start(0) stops the timer", async () => {
    const w = new ApprovalWatcher({ client: () => undefined, workspaceName: (x) => x, notify: (n) => notices.push(n), seen: memStore(), now: () => now });
    await w.poll(); expect(notices).toEqual([]); expect(srv.calls.length).toBe(0);
    w.start(1); w.start(0); await new Promise((r) => setTimeout(r, 20)); expect(srv.calls.length).toBe(0); w.dispose();
  });
});
```

- [ ] **Step 2: Run** `npm test` → module not found.

- [ ] **Step 3: Implement**

```ts
// services/vscode/src/notifications/approvals.ts
import type { TdtClient } from "../api/client";
import type { GraphSummary, Run } from "../api/types";

export interface SeenStore { get(): Record<string, number> | undefined; set(v: Record<string, number>): Promise<void> }
export interface ApprovalNotice { run: Run; workspaceName: string; summary?: GraphSummary }

/** Polls for runs awaiting approval and raises each one once (per 24 h, across reloads). Pure: no vscode import. */
export class ApprovalWatcher {
  private timer: NodeJS.Timeout | undefined;
  private inflight: Promise<void> | null = null;
  private readonly ttl: number;
  private readonly now: () => number;
  constructor(private readonly d: { client: () => TdtClient | undefined; workspaceName: (id: string) => string; notify: (n: ApprovalNotice) => void; seen: SeenStore; now?: () => number; trace?: (l: string) => void; ttlMs?: number }) {
    this.ttl = d.ttlMs ?? 24 * 3_600_000; this.now = d.now ?? (() => Date.now());
  }
  private seen(): Record<string, number> { const v = this.d.seen.get() ?? {}; const cutoff = this.now() - this.ttl; return Object.fromEntries(Object.entries(v).filter(([, t]) => t >= cutoff)); }
  private async fetchAwaiting(): Promise<Run[] | undefined> {
    const c = this.d.client(); if (!c) return undefined;
    try { return await c.listRuns({ status: ["awaiting_approval"], limit: 100 }); }
    catch (e) { this.d.trace?.(`approvals poll failed: ${e instanceof Error ? e.message : String(e)}`); return undefined; }
  }
  /** Record everything currently awaiting as seen, notifying nobody (first activation / sign-in). */
  async prime(): Promise<void> {
    const runs = await this.fetchAwaiting(); if (!runs) return;
    const seen = this.seen(); const t = this.now(); for (const r of runs) seen[r.id] = seen[r.id] ?? t;
    await this.d.seen.set(seen);
  }
  poll(): Promise<void> { if (!this.inflight) this.inflight = this.doPoll().finally(() => { this.inflight = null; }); return this.inflight; }
  private async doPoll(): Promise<void> {
    const runs = await this.fetchAwaiting(); if (!runs) return;
    const seen = this.seen(); const fresh = runs.filter((r) => !(r.id in seen)); const t = this.now();
    for (const r of fresh) seen[r.id] = t;
    if (fresh.length || Object.keys(seen).length !== Object.keys(this.d.seen.get() ?? {}).length) await this.d.seen.set(seen);
    const c = this.d.client();
    for (const r of fresh) {
      let summary: GraphSummary | undefined;
      try { summary = c ? (await c.getGraph(r.id)).summary : undefined; } catch { summary = undefined; }
      this.d.notify({ run: r, workspaceName: this.d.workspaceName(r.workspace_id), summary });
    }
  }
  start(intervalMs: number) { this.stop(); if (intervalMs <= 0) return; const tick = async () => { await this.poll(); this.timer = setTimeout(tick, intervalMs); }; this.timer = setTimeout(tick, intervalMs); }
  stop() { if (this.timer) clearTimeout(this.timer); this.timer = undefined; }
  dispose() { this.stop(); }
}
```

- [ ] **Step 4: Run** tests (twice), typecheck, build.
- [ ] **Step 5: Commit** — `feat(vscode): approval watcher with per-run dedupe persisted for 24h` + trailers.

---

### Task 2: Wiring, setting, docs, version

**Files:**
- Modify: `services/vscode/src/extension.ts`, `services/vscode/package.json` (setting `terraducktel.approvals.pollSeconds`, version `0.3.0`), `docs/VSCODE.md` ("Approval notifications" section), spec §6 (a "Milestone C notes" list: prime-on-sign-in suppresses the backlog; 24 h dedupe in `globalState`; polling independent of sidebar visibility but off while signed out; minimum 15 s), `test/integration/suite/smoke.test.ts` (assert the setting exists via `vscode.workspace.getConfiguration("terraducktel").inspect("approvals.pollSeconds")?.defaultValue === 60`).

- [ ] **Step 1: Wire in `extension.ts`** after the `EditorStatus` block:

```ts
  const seenStore = { get: () => context.globalState.get<Record<string, number>>("terraducktel.approvals.seen"), set: (v: Record<string, number>) => Promise.resolve(context.globalState.update("terraducktel.approvals.seen", v)) };
  const approvals = new ApprovalWatcher({
    client: () => (session.tokens?.isSignedIn() ? session.client : undefined),
    workspaceName: (id) => session.store.workspace(id)?.name ?? id.slice(0, 8),
    seen: seenStore,
    trace: (l) => { if (vscode.workspace.getConfiguration("terraducktel").get<boolean>("trace")) session.log.appendLine(l); },
    notify: ({ run, workspaceName, summary }) => {
      const s = summary ? ` (+${summary.add ?? 0} ~${summary.change ?? 0} -${summary.destroy ?? 0})` : "";
      void vscode.window.showInformationMessage(`TDT: ${workspaceName} ${run.command} is awaiting approval${s}.`, "Approve…", "Reject…", "Open").then((a) => {
        const node = new RunNode(run, { showWorkspace: workspaceName });
        if (a === "Approve…") void vscode.commands.executeCommand("terraducktel.approve", node);
        else if (a === "Reject…") void vscode.commands.executeCommand("terraducktel.reject", node);
        else if (a === "Open") void vscode.commands.executeCommand("terraducktel.openInBrowser", node);
      });
    },
  });
  context.subscriptions.push(approvals);
  const approvalsInterval = () => { const s = vscode.workspace.getConfiguration("terraducktel").get<number>("approvals.pollSeconds", 60); return s <= 0 ? 0 : Math.max(15, s) * 1000; };
  let primedFor: string | undefined;
  const rearm = async () => {
    const key = session.tokens?.isSignedIn() ? `${session.profile?.name}:${session.bu}` : undefined;
    if (!key) { approvals.stop(); primedFor = undefined; return; }
    if (primedFor !== key) { primedFor = key; await approvals.prime(); }   // never spray the backlog after sign-in / BU switch
    approvals.start(approvalsInterval());
  };
  context.subscriptions.push(session.onDidChange(() => void rearm()),
    vscode.workspace.onDidChangeConfiguration((e) => { if (e.affectsConfiguration("terraducktel.approvals")) void rearm(); }));
  void rearm();
```
(imports: `ApprovalWatcher` from `./notifications/approvals`, `RunNode` from `./views/nodes`.) Note `session.onDidChange` fires on sign-in/out, BU switch and reload, so `rearm()` covers all transitions; `prime()` runs once per profile+BU.

- [ ] **Step 2: Manifest** — setting `terraducktel.approvals.pollSeconds` `{ "type": "number", "default": 60, "minimum": 0, "description": "How often to check for runs awaiting approval and raise a notification (seconds). 0 disables; values below 15 are treated as 15." }`; `"version": "0.3.0"`.

- [ ] **Step 3: Docs + spec + smoke assertion.** `docs/VSCODE.md` "Approval notifications": what fires, the actions, dedupe (once per run per 24 h, across reloads), the prime behaviour (nothing fires for runs already waiting when you sign in — the Runs badge shows those), the setting. Spec §6 notes as listed above.

- [ ] **Step 4: Verify** `npm run typecheck && npm test && npm run build && npm run test:integration && npm run package`.

- [ ] **Step 5: Commit** — `feat(vscode): approval notifications; bump to 0.3.0` + trailers.

---

## Self-review checklist (done while writing)

- **Spec §6 coverage:** background poll on the active BU (T1/T2), configurable interval with 0 = off (T2), dedupe per run incl. 24 h persistence (T1), notification with Approve/Reject/Open where Approve goes through the modal (T2 reuses `terraducktel.approve`), badge already present from A.
- **Type consistency:** `SeenStore.get/set` in T1 matches the `globalState` adapter in T2; `ApprovalNotice` fields used by T2's `notify`; `TokenProvider.hasCredential` present in the test fake (added in A's follow-up).
- **Deliberate choices recorded in spec notes:** prime-on-sign-in; minimum 15 s; polling not gated on visibility; failures silent.
