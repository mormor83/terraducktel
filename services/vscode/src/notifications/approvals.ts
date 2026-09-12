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
