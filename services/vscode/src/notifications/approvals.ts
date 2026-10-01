import type { TdtClient } from "../api/client";
import type { BusinessUnit, GraphSummary, Run } from "../api/types";
import { mapLimit } from "../state/mapLimit";

/** At most this many BUs polled at once (one request each). */
const POLL_CONCURRENCY = 4;

export interface SeenStore { get(): Record<string, number> | undefined; set(v: Record<string, number>): Promise<void> }
export interface ApprovalNotice { run: Run; bu: BusinessUnit; workspaceName: string; summary?: GraphSummary }

/** Toast text: names the workspace AND its business unit, since runs now come from several BUs. */
export function approvalMessage(n: ApprovalNotice): string {
  const sm = n.summary ? ` (+${n.summary.add ?? 0} ~${n.summary.change ?? 0} -${n.summary.destroy ?? 0})` : "";
  return `TDT: ${n.workspaceName} (${n.bu.name || n.bu.slug}) ${n.run.command} is awaiting approval${sm}.`;
}

interface BuRuns { bu: BusinessUnit; runs: Run[] }

/** Polls every visible BU for runs awaiting approval and raises each one once (per 24 h, across reloads). Pure: no vscode import. */
export class ApprovalWatcher {
  private timer: NodeJS.Timeout | undefined;
  private inflight: Promise<void> | null = null;
  /** Bumped by every stop(); a tick re-arms only while its own epoch is still current, so a
   *  stop() (or a second start()) that lands while a poll is in flight cannot be undone by the
   *  `finally` of the tick it interrupted — which would otherwise leave an orphan chain running
   *  past dispose(). */
  private loopEpoch = 0;
  /** BU slugs whose current backlog has been recorded as seen. A BU outside this set NEVER
   *  notifies: the poll records whatever it fetched for it and adds it. That covers a prime() that
   *  failed for that BU (network down at wake-up), a poll whose response lands before a concurrent
   *  prime()'s, and a BU that only just became visible — in every case the first thing a fresh
   *  BU gets is its backlog swallowed. */
  private primed = new Set<string>();
  private readonly ttl: number;
  private readonly now: () => number;
  constructor(private readonly d: { client: () => TdtClient | undefined; /** Slugs the user filtered out; those BUs are not polled. */ hidden?: () => readonly string[]; workspaceName: (id: string) => string; notify: (n: ApprovalNotice) => void; seen: SeenStore; now?: () => number; trace?: (l: string) => void; ttlMs?: number }) {
    this.ttl = d.ttlMs ?? 24 * 3_600_000; this.now = d.now ?? (() => Date.now());
  }
  private seen(): Record<string, number> { const v = this.d.seen.get() ?? {}; const cutoff = this.now() - this.ttl; return Object.fromEntries(Object.entries(v).filter(([, t]) => t >= cutoff)); }
  /** Awaiting runs per visible BU (one `GET /business-units`, then each BU with its own header).
   *  `undefined` when the BU list itself is unavailable; a BU whose fetch fails is simply left out
   *  (it is not primed, so it can't notify, and its failure never silences the others). */
  private async fetchAwaiting(): Promise<BuRuns[] | undefined> {
    const c = this.d.client(); if (!c) return undefined;
    try {
      const hidden = new Set(this.d.hidden?.() ?? []);
      const bus = (await c.listBusinessUnits()).filter((b) => !hidden.has(b.slug));
      const out = await mapLimit(bus, POLL_CONCURRENCY, async (bu): Promise<BuRuns | undefined> => {
        try { return { bu, runs: await c.withBu(bu.slug).listRuns({ status: ["awaiting_approval"], limit: 100 }) }; }
        catch (e) { this.trace(`approvals poll failed for ${bu.slug}: ${e instanceof Error ? e.message : String(e)}`); return undefined; }
      });
      return out.filter((x): x is BuRuns => !!x);
    } catch (e) { this.trace(`approvals poll failed: ${e instanceof Error ? e.message : String(e)}`); return undefined; }
  }
  /** Record everything currently awaiting as seen, notifying nobody (first activation / sign-in). */
  async prime(): Promise<void> {
    this.primed.clear();
    const got = await this.fetchAwaiting(); if (!got) return;
    await this.recordSilently(got);
  }
  /** Swallow the fetched runs into the seen set and mark their BUs primed — but only once the write
   *  actually landed. A store we could not persist to would otherwise let the very next poll
   *  treat the whole backlog as fresh; and a rejecting store must never reject rearm(). */
  private async recordSilently(got: BuRuns[]): Promise<void> {
    const seen = this.seen(); const t = this.now(); for (const { runs } of got) for (const r of runs) seen[r.id] = seen[r.id] ?? t;
    try { await this.d.seen.set(seen); for (const { bu } of got) this.primed.add(bu.slug); }
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
    const got = await this.fetchAwaiting(); if (!got) return;
    const unprimed = got.filter((g) => !this.primed.has(g.bu.slug));
    if (unprimed.length) await this.recordSilently(unprimed);
    const ready = got.filter((g) => this.primed.has(g.bu.slug) && !unprimed.includes(g));
    const seen = this.seen(); const t = this.now();
    const fresh = ready.flatMap(({ bu, runs }) => runs.filter((r) => !(r.id in seen)).map((run) => ({ bu, run })));
    for (const { run } of fresh) seen[run.id] = t;
    if (fresh.length || Object.keys(seen).length !== Object.keys(this.d.seen.get() ?? {}).length) {
      // A rejecting persistence call must not stop the runs below from being notified, nor
      // take down the poll loop that called us.
      try { await this.d.seen.set(seen); }
      catch (e) { this.trace(`approvals seen.set failed: ${e instanceof Error ? e.message : String(e)}`); }
    }
    const c = this.d.client();
    for (const { bu, run: r } of fresh) {
      let summary: GraphSummary | undefined;
      try { summary = c ? (await c.withBu(bu.slug).getGraph(r.id)).summary : undefined; }
      catch (e) { summary = undefined; this.trace(`approvals getGraph failed: ${e instanceof Error ? e.message : String(e)}`); }
      // One run's notify() throwing (e.g. a flaky showInformationMessage) must not swallow the
      // rest of this batch — each run gets its own try/catch.
      try { this.d.notify({ run: r, bu, workspaceName: this.d.workspaceName(r.workspace_id), summary }); }
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
