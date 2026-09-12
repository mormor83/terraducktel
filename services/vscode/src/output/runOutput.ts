import * as vscode from "vscode";
import type { TdtClient } from "../api/client";
import { PLAN_LANDED_STATUSES, type Run, type RunStep } from "../api/types";

export interface LineSink { appendLine(line: string): void }
export interface TailOptions { pollMs?: number; isCancelled?: () => boolean; timeoutMs?: number }

/** Number of newline-terminated lines of `output` already fully printed for a step. */
function linesOf(output: string): string[] {
  const norm = output.replace(/\n+$/, "");
  return norm.length ? norm.split("\n") : [];
}

/** Follow a run: print each step once (header + output), re-poll only from the first
 *  unfinished step (`since`) so its growing output keeps arriving, but never reprint a
 *  step header or output already printed. Stops when the run lands (terminal or
 *  awaiting_approval), is cancelled, or times out. */
export async function tailRun(client: TdtClient, runId: string, sink: LineSink, opts: TailOptions = {}): Promise<Run> {
  const pollMs = opts.pollMs ?? 2000;
  const deadline = Date.now() + (opts.timeoutMs ?? 3 * 60 * 60_000);
  let since = 0;
  const headerPrinted = new Set<number>();
  const printedCount = new Map<number, number>(); // position → number of lines already printed

  const applySteps = (steps: RunStep[]) => {
    const sorted = [...steps].sort((a, b) => a.position - b.position);
    for (const st of sorted) {
      if (!headerPrinted.has(st.position)) {
        sink.appendLine(`── ${st.name} [${st.status}]`);
        headerPrinted.add(st.position);
        printedCount.set(st.position, 0);
      }
      const lines = linesOf(st.output ?? "");
      const done = printedCount.get(st.position) ?? 0;
      if (lines.length > done) {
        for (const l of lines.slice(done)) sink.appendLine(l);
        printedCount.set(st.position, lines.length);
      }
    }
    if (sorted.length) {
      const firstUnfinished = sorted.find((s) => s.status === "pending" || s.status === "running");
      since = firstUnfinished ? firstUnfinished.position : sorted[sorted.length - 1].position + 1;
    }
  };

  for (;;) {
    applySteps(await client.getSteps(runId, since));
    const run = await client.getRun(runId);
    if (PLAN_LANDED_STATUSES.has(run.status)) {
      // The steps fetched above may still lag one status transition behind the run itself
      // (the run flips to its landed status between our steps call and our run call) — do
      // one last flush so the final step output/status makes it into the sink before we return.
      applySteps(await client.getSteps(runId, since));
      sink.appendLine(`── run ${run.status}`);
      return run;
    }
    if (opts.isCancelled?.() || Date.now() > deadline) return run;
    await new Promise((r) => setTimeout(r, pollMs));
  }
}

/** One OutputChannel per run; re-watching an already-active run just reveals it. */
export class RunOutputManager implements vscode.Disposable {
  private channels = new Map<string, { ch: vscode.OutputChannel; active: boolean; cancel: () => void }>();

  watch(client: TdtClient, runId: string, title: string, onLanded?: (run: Run) => void): void {
    const existing = this.channels.get(runId);
    if (existing) { existing.ch.show(true); if (existing.active) return; }
    const ch = existing?.ch ?? vscode.window.createOutputChannel(`TDT run ${runId.slice(0, 8)} — ${title}`);
    let cancelled = false;
    const entry = { ch, active: true, cancel: () => { cancelled = true; } };
    this.channels.set(runId, entry);
    ch.clear();
    ch.show(true);
    void vscode.window.withProgress(
      { location: vscode.ProgressLocation.Window, title: `TDT: watching ${title}`, cancellable: true },
      async (_progress, token) => {
        // Same flag either way: an explicit dispose() (entry.cancel()) and the progress
        // notification's own cancel button must stop the loop identically.
        token.onCancellationRequested(() => (cancelled = true));
        try {
          const run = await tailRun(client, runId, ch, { isCancelled: () => cancelled });
          entry.active = false;
          if (!cancelled) onLanded?.(run);
        } catch (e) {
          entry.active = false;
          // Once cancelled, the channel may already be disposed (dispose() cancels every
          // entry before disposing its channel) — never touch it after that point.
          if (!cancelled) ch.appendLine(`✕ ${e instanceof Error ? e.message : String(e)}`);
        }
      },
    );
  }

  dispose() {
    // Cancel every in-flight tail loop BEFORE disposing its channel, so no loop still
    // running after this call can `appendLine`/`show` on an already-disposed channel.
    for (const entry of this.channels.values()) { entry.cancel(); entry.ch.dispose(); }
    this.channels.clear();
  }
}
