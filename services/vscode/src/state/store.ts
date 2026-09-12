import { EventEmitter } from "vscode";
import type { TdtClient } from "../api/client";
import type { Run, Workspace } from "../api/types";

/** Polling cache of the current BU's workspaces + runs. One in-flight refresh at a time;
 *  keeps the last good snapshot on failure and backs off after repeated failures. */
export class Store {
  workspaces: Workspace[] = [];
  runs: Run[] = [];
  private byWs = new Map<string, Run[]>();
  lastError: Error | undefined;
  consecutiveFailures = 0;
  private inflight: Promise<void> | null = null;
  private timer: NodeJS.Timeout | undefined;
  private changed = new EventEmitter<void>();
  readonly onDidChange = this.changed.event;

  constructor(private readonly client: () => TdtClient | undefined, private readonly opts: { runsLimit: number }) {}

  runsFor(wsId: string): Run[] { return this.byWs.get(wsId) ?? []; }
  workspace(id: string) { return this.workspaces.find((w) => w.id === id); }
  run(id: string) { return this.runs.find((r) => r.id === id); }

  refresh(): Promise<void> {
    if (this.inflight) return this.inflight;
    this.inflight = this.doRefresh().finally(() => { this.inflight = null; });
    return this.inflight;
  }
  private async doRefresh() {
    const c = this.client();
    if (!c) { this.workspaces = []; this.runs = []; this.byWs.clear(); this.changed.fire(); return; }
    try {
      const [ws, runs] = await Promise.all([c.listWorkspaces(), c.listRuns({ limit: this.opts.runsLimit })]);
      this.workspaces = ws;
      this.runs = [...runs].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
      this.byWs = new Map(); for (const r of this.runs) { const l = this.byWs.get(r.workspace_id) ?? []; l.push(r); this.byWs.set(r.workspace_id, l); }
      this.lastError = undefined; this.consecutiveFailures = 0;
    } catch (e) {
      this.lastError = e instanceof Error ? e : new Error(String(e)); this.consecutiveFailures++;
    }
    this.changed.fire();
  }
  clear() { this.workspaces = []; this.runs = []; this.byWs.clear(); this.lastError = undefined; this.consecutiveFailures = 0; this.changed.fire(); }

  /** Poll every `intervalMs`; after 3 consecutive failures stretch to 5 minutes until one succeeds. */
  start(intervalMs: number) {
    this.stop();
    const tick = async () => { await this.refresh(); const wait = this.consecutiveFailures >= 3 ? 5 * 60_000 : intervalMs; this.timer = setTimeout(tick, wait); };
    this.timer = setTimeout(tick, intervalMs);
  }
  stop() { if (this.timer) clearTimeout(this.timer); this.timer = undefined; }
  dispose() { this.stop(); this.changed.dispose(); }
}
