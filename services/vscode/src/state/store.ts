import { EventEmitter } from "vscode";
import type { TdtClient } from "../api/client";
import type { BusinessUnit, Run, Workspace } from "../api/types";
import { mapLimit } from "./mapLimit";

/** One business unit's slice of the cache. `error` is that BU's last fetch failure (its previous
 *  data is kept next to it); `loaded` is false until the first successful fetch. */
export interface BuData { bu: BusinessUnit; workspaces: Workspace[]; runs: Run[]; error?: string; loaded: boolean }

/** At most this many HTTP requests in flight per refresh (workspaces and runs of all BUs share it). */
const REQUEST_CONCURRENCY = 4;
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const newestFirst = (a: Run, b: Run) => (b.created_at ?? "").localeCompare(a.created_at ?? "");

/** Polling cache of every visible business unit's workspaces + runs, fetched per BU with that BU's
 *  `X-Business-Unit` header. One in-flight refresh at a time; keeps the last good snapshot on
 *  failure (per BU, so one BU's error never blanks another) and backs off after repeated failures. */
export class Store {
  /** Every BU the user can access (from `GET /business-units`), hidden ones included. */
  bus: BusinessUnit[] = [];
  /** Data for the VISIBLE BUs only, in list order; hidden BUs are neither fetched nor kept. */
  data = new Map<string, BuData>();
  /** A failure of the BU list itself (auth, network) — the trees keep showing the last good data. */
  globalError: Error | undefined;
  consecutiveFailures = 0;
  private byWs = new Map<string, Run[]>();
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

  constructor(private readonly client: () => TdtClient | undefined, private readonly opts: () => { runsLimit: number }, private readonly hidden: () => readonly string[] = () => []) {}

  visibleBus(): BusinessUnit[] { const h = new Set(this.hidden()); return this.bus.filter((b) => !h.has(b.slug)); }
  /** Workspaces / runs of all loaded (visible) BUs, flattened — runs newest first. */
  get workspaces(): Workspace[] { return [...this.data.values()].flatMap((d) => d.workspaces); }
  get runs(): Run[] { return [...this.data.values()].flatMap((d) => d.runs).sort(newestFirst); }
  runsFor(wsId: string): Run[] { return this.byWs.get(wsId) ?? []; }
  findWorkspace(id: string): { ws: Workspace; bu: BusinessUnit } | undefined { for (const d of this.data.values()) { const ws = d.workspaces.find((w) => w.id === id); if (ws) return { ws, bu: d.bu }; } return undefined; }
  findRun(id: string): { run: Run; bu: BusinessUnit } | undefined { for (const d of this.data.values()) { const run = d.runs.find((r) => r.id === id); if (run) return { run, bu: d.bu }; } return undefined; }
  workspace(id: string) { return this.findWorkspace(id)?.ws; }

  refresh(): Promise<void> {
    if (this.inflight) return this.inflight;
    this.inflight = this.doRefresh().finally(() => { this.inflight = null; });
    return this.inflight;
  }
  private reindex() {
    this.byWs = new Map();
    for (const d of this.data.values()) for (const r of d.runs) { const l = this.byWs.get(r.workspace_id) ?? []; l.push(r); this.byWs.set(r.workspace_id, l); }
  }
  /** Refetches with whatever client / filter is current when the fetches resolve. If `Session`
   *  swaps the client (profile change) or the user changes the BU filter while a fetch is in
   *  flight, the just-landed data belongs to the OLD state and must never be applied — instead
   *  retry immediately. Bounded because those only change a finite number of times per user
   *  action; the hard cap below is just a backstop against a pathological getter that never settles. */
  private async doRefresh() {
    const MAX_RETRIES = 5;
    const hiddenKey = () => [...this.hidden()].sort().join("\n");
    for (let attempt = 0; ; attempt++) {
      const c = this.client();
      if (!c) { this.clear(); return; }
      const hk = hiddenKey();
      const stale = () => attempt < MAX_RETRIES && (this.client() !== c || hiddenKey() !== hk);
      let bus: BusinessUnit[];
      try { bus = await c.listBusinessUnits(); }
      catch (e) {
        if (stale()) continue;
        this.globalError = e instanceof Error ? e : new Error(String(e)); this.consecutiveFailures++;
        break;
      }
      if (stale()) continue;
      const hidden = new Set(this.hidden());
      const visibleBus = bus.filter((b) => !hidden.has(b.slug));
      // One task per (BU, endpoint) so the cap bounds requests, not BUs (a BU needs two of them).
      const tasks = visibleBus.flatMap((bu) => [{ bu, kind: "workspaces" as const }, { bu, kind: "runs" as const }]);
      type Fetched = { workspaces: Workspace[] } | { runs: Run[] } | { error: string };
      const settled: Fetched[] = await mapLimit(tasks, REQUEST_CONCURRENCY, async ({ bu, kind }): Promise<Fetched> => {
        const bc = c.withBu(bu.slug);
        try { return kind === "workspaces" ? { workspaces: await bc.listWorkspaces() } : { runs: await bc.listRuns({ limit: this.opts().runsLimit }) }; }
        catch (e) { return { error: errMsg(e) }; }
      });
      const results = visibleBus.map((bu, i): { bu: BusinessUnit; workspaces: Workspace[]; runs: Run[] } | { bu: BusinessUnit; error: string } => {
        const w = settled[2 * i], r = settled[2 * i + 1];
        if ("error" in w) return { bu, error: w.error };
        if ("error" in r) return { bu, error: r.error };
        return { bu, workspaces: "workspaces" in w ? w.workspaces : [], runs: "runs" in r ? r.runs : [] };
      });
      if (stale()) continue; // a newer client / filter took over while these fetches were in flight
      const next = new Map<string, BuData>();
      for (const r of results) {
        if ("error" in r) next.set(r.bu.slug, { ...(this.data.get(r.bu.slug) ?? { workspaces: [], runs: [], loaded: false }), bu: r.bu, error: r.error });
        else next.set(r.bu.slug, { bu: r.bu, workspaces: r.workspaces, runs: [...r.runs].sort(newestFirst), loaded: true });
      }
      this.bus = bus; this.data = next; this.reindex(); this.globalError = undefined;
      // Backoff only when EVERY visible BU failed — a single flaky BU must not slow the others.
      this.consecutiveFailures = results.length && results.every((r) => "error" in r) ? this.consecutiveFailures + 1 : 0;
      break;
    }
    this.changed.fire();
  }
  clear() { this.bus = []; this.data = new Map(); this.byWs.clear(); this.globalError = undefined; this.consecutiveFailures = 0; this.changed.fire(); }

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
