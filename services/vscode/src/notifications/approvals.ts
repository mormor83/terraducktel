import type { TdtClient } from "../api/client";
import type { GraphSummary, Run } from "../api/types";

export interface SeenStore { get(): Record<string, number> | undefined; set(v: Record<string, number>): Promise<void> }
export interface ApprovalNotice { run: Run; workspaceName: string; summary?: GraphSummary }

/** Polls for runs awaiting approval and raises each one once (per 24 h, across reloads). Pure: no vscode import. */
export class ApprovalWatcher {
  private timer: NodeJS.Timeout | undefined;
  private inflight: Promise<void> | null = null;
  /** Bumped by every stop(); a tick re-arms only while its own epoch is still current, so a
   *  stop() (or a second start()) that lands while a poll is in flight cannot be undone by the
   *  `finally` of the tick it interrupted — which would otherwise leave an orphan chain running
   *  past dispose(). */
  private loopEpoch = 0;
  /** False until the current backlog has been recorded as seen. While false the watcher NEVER
   *  notifies: it records whatever it fetched and flips to true. That covers both a prime() that
   *  failed (network down at wake-up) and a poll whose response lands before a concurrent
   *  prime()'s — either way the first thing a fresh session does is swallow the backlog. */
  private primed = false;
  private readonly ttl: number;
  private readonly now: () => number;
  constructor(private readonly d: { client: () => TdtClient | undefined; workspaceName: (id: string) => string; notify: (n: ApprovalNotice) => void; seen: SeenStore; now?: () => number; trace?: (l: string) => void; ttlMs?: number }) {
    this.ttl = d.ttlMs ?? 24 * 3_600_000; this.now = d.now ?? (() => Date.now());
  }
  private seen(): Record<string, number> { const v = this.d.seen.get() ?? {}; const cutoff = this.now() - this.ttl; return Object.fromEntries(Object.entries(v).filter(([, t]) => t >= cutoff)); }
  private async fetchAwaiting(): Promise<Run[] | undefined> {
    const c = this.d.client(); if (!c) return undefined;
    try { return await c.listRuns({ status: ["awaiting_approval"], limit: 100 }); }
    catch (e) { this.trace(`approvals poll failed: ${e instanceof Error ? e.message : String(e)}`); return undefined; }
  }
  /** Record everything currently awaiting as seen, notifying nobody (first activation / sign-in). */
  async prime(): Promise<void> {
    this.primed = false;
    const runs = await this.fetchAwaiting(); if (!runs) return;
    await this.recordSilently(runs);
  }
  /** Swallow `runs` into the seen set and mark the watcher primed — but only once the write
   *  actually landed. A store we could not persist to would otherwise let the very next poll
   *  treat the whole backlog as fresh; and a rejecting store must never reject rearm(). */
  private async recordSilently(runs: Run[]): Promise<void> {
    const seen = this.seen(); const t = this.now(); for (const r of runs) seen[r.id] = seen[r.id] ?? t;
    try { await this.d.seen.set(seen); this.primed = true; }
    catch (e) { this.trace(`approvals prime seen.set failed: ${e instanceof Error ? e.message : String(e)}`); }
  }
  /** Mark one run as already announced. The run-output tail raises its own "awaiting approval"
   *  toast for runs started from this window; without this the poll loop would announce the very
   *  same run a second time. */
  async markSeen(runId: string): Promise<void> {
    const seen = this.seen(); seen[runId] = this.now();
    try { await this.d.seen.set(seen); }
    catch (e) { this.trace(`approvals markSeen failed: ${e instanceof Error ? e.message : String(e)}`); }
  }
  poll(): Promise<void> { if (!this.inflight) this.inflight = this.doPoll().finally(() => { this.inflight = null; }); return this.inflight; }
  /** Never let a throwing trace callback itself break the caller — trace is diagnostics only. */
  private trace(l: string) { try { this.d.trace?.(l); } catch { /* diagnostics only */ } }
  private async doPoll(): Promise<void> {
    const runs = await this.fetchAwaiting(); if (!runs) return;
    if (!this.primed) { await this.recordSilently(runs); return; }
    const seen = this.seen(); const fresh = runs.filter((r) => !(r.id in seen)); const t = this.now();
    for (const r of fresh) seen[r.id] = t;
    if (fresh.length || Object.keys(seen).length !== Object.keys(this.d.seen.get() ?? {}).length) {
      // A rejecting persistence call must not stop the runs below from being notified, nor
      // take down the poll loop that called us.
      try { await this.d.seen.set(seen); }
      catch (e) { this.trace(`approvals seen.set failed: ${e instanceof Error ? e.message : String(e)}`); }
    }
    const c = this.d.client();
    for (const r of fresh) {
      let summary: GraphSummary | undefined;
      try { summary = c ? (await c.getGraph(r.id)).summary : undefined; }
      catch (e) { summary = undefined; this.trace(`approvals getGraph failed: ${e instanceof Error ? e.message : String(e)}`); }
      // One run's notify() throwing (e.g. a flaky showInformationMessage) must not swallow the
      // rest of this batch — each run gets its own try/catch.
      try { this.d.notify({ run: r, workspaceName: this.d.workspaceName(r.workspace_id), summary }); }
      catch (e) { this.trace(`approvals notify failed: ${e instanceof Error ? e.message : String(e)}`); }
    }
  }
  start(intervalMs: number) {
    this.stop(); if (intervalMs <= 0) return;
    const e = this.loopEpoch;
    const tick = async () => {
      // The loop must keep ticking even if this poll rejected outright (fetchAwaiting already
      // swallows its own errors, but guard the whole call in case a future change doesn't) — and
      // even if the trace() call itself throws, so the reschedule lives in `finally`. The epoch
      // check is what keeps `finally` from resurrecting a loop stopped mid-poll.
      try { await this.poll(); }
      catch (err) { this.trace(`approvals poll failed: ${err instanceof Error ? err.message : String(err)}`); }
      finally { if (this.loopEpoch === e) this.timer = setTimeout(tick, intervalMs); }
    };
    this.timer = setTimeout(tick, intervalMs);
  }
  stop() { this.loopEpoch++; if (this.timer) clearTimeout(this.timer); this.timer = undefined; }
  dispose() { this.stop(); }
}
