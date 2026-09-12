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
  /** Bumped by every stop(); a tick re-arms only while its own epoch is still current. Without
   *  it, a stop() landing during the tick's `await refresh()` is undone by the reschedule that
   *  follows it — and a second start() mid-refresh leaves two chains polling forever. */
  private loopEpoch = 0;
  /** Whether a view is on screen. The timer keeps ticking while inactive but skips the network:
   *  polling a sidebar nobody is looking at is pure load on the API. Manual `refresh()` (the
   *  title-bar button, a command, a just-landed run) is never gated by this. */
  private active = true;
  private changed = new EventEmitter<void>();
  readonly onDidChange = this.changed.event;

  constructor(private readonly client: () => TdtClient | undefined, private readonly opts: () => { runsLimit: number }) {}

  runsFor(wsId: string): Run[] { return this.byWs.get(wsId) ?? []; }
  workspace(id: string) { return this.workspaces.find((w) => w.id === id); }
  run(id: string) { return this.runs.find((r) => r.id === id); }

  refresh(): Promise<void> {
    if (this.inflight) return this.inflight;
    this.inflight = this.doRefresh().finally(() => { this.inflight = null; });
    return this.inflight;
  }
  /** Refetches with whatever client is current when the fetch resolves. If `Session` swaps the
   *  client (profile/BU change) while a fetch is in flight, the just-landed data belongs to the
   *  OLD client/BU and must never be applied — instead retry immediately with the now-current
   *  client. Bounded because a client only changes a finite number of times per user action; the
   *  hard cap below is just a backstop against a pathological getter that never settles. */
  private async doRefresh() {
    const MAX_RETRIES = 5;
    for (let attempt = 0; ; attempt++) {
      const c = this.client();
      if (!c) { this.workspaces = []; this.runs = []; this.byWs.clear(); this.lastError = undefined; this.consecutiveFailures = 0; this.changed.fire(); return; }
      const stale = () => attempt < MAX_RETRIES && this.client() !== c;
      try {
        const [ws, runs] = await Promise.all([c.listWorkspaces(), c.listRuns({ limit: this.opts().runsLimit })]);
        if (stale()) continue; // a newer client took over while this fetch was in flight
        this.workspaces = ws;
        this.runs = [...runs].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
        this.byWs = new Map(); for (const r of this.runs) { const l = this.byWs.get(r.workspace_id) ?? []; l.push(r); this.byWs.set(r.workspace_id, l); }
        this.lastError = undefined; this.consecutiveFailures = 0;
      } catch (e) {
        if (stale()) continue; // stale error from a superseded client; retry with the current one
        this.lastError = e instanceof Error ? e : new Error(String(e)); this.consecutiveFailures++;
      }
      break;
    }
    this.changed.fire();
  }
  clear() { this.workspaces = []; this.runs = []; this.byWs.clear(); this.lastError = undefined; this.consecutiveFailures = 0; this.changed.fire(); }

  setActive(active: boolean) { this.active = active; }
  isActive() { return this.active; }

  /** Poll every `intervalMs` while a view is visible; after 3 consecutive failures stretch to
   *  5 minutes until one succeeds. */
  start(intervalMs: number) {
    this.stop();
    const e = this.loopEpoch;
    const tick = async () => {
      try { if (this.active) await this.refresh(); }
      finally { if (this.loopEpoch === e) this.timer = setTimeout(tick, this.consecutiveFailures >= 3 ? 5 * 60_000 : intervalMs); }
    };
    this.timer = setTimeout(tick, intervalMs);
  }
  stop() { this.loopEpoch++; if (this.timer) clearTimeout(this.timer); this.timer = undefined; }
  dispose() { this.stop(); this.changed.dispose(); }
}
