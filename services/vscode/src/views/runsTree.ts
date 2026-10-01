import * as vscode from "vscode";
import type { BusinessUnit, Run } from "../api/types";
import { TERMINAL_RUN_STATUSES } from "../api/types";
import type { Session } from "../session";
import { BuNode, MessageNode, RunNode, StepNode, type Node } from "./nodes";

const ORDER = ["awaiting_approval", "applying", "running", "planning", "pending", "planned", "failed", "applied", "cancelled"];
/** Unknown / future statuses sort after every known one instead of ahead of them (`indexOf` → -1). */
const rank = (status: string) => { const i = ORDER.indexOf(status); return i === -1 ? ORDER.length : i; };
const byName = (a: BusinessUnit, b: BusinessUnit) => (a.name || a.slug).localeCompare(b.name || b.slug);
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Badge for the Workspaces view (and so the activity-bar icon): runs waiting at the gate. */
export function awaitingBadge(runs: Run[]): vscode.ViewBadge | undefined {
  const n = runs.filter((r) => r.status === "awaiting_approval").length;
  return n ? { value: n, tooltip: `${n} awaiting approval` } : undefined;
}

/** Roots are the visible business units; a BU's children are its own runs. */
export class RunsTree implements vscode.TreeDataProvider<Node> {
  private changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  /** Steps of runs that have reached a terminal status — those can never change again, so an
   *  expanded run is fetched at most once. Live runs are re-fetched on every expand. */
  private steps = new Map<string, Node[]>();
  constructor(private readonly s: Session) {
    s.store.onDidChange(() => { this.prune(); this.changed.fire(undefined); });
    s.onDidChange(() => { this.steps.clear(); this.changed.fire(undefined); });
  }
  private prune() { const live = new Set(this.s.store.runs.map((r) => r.id)); for (const id of [...this.steps.keys()]) if (!live.has(id)) this.steps.delete(id); }

  getTreeItem(n: Node) { return n; }
  async getChildren(n?: Node): Promise<Node[]> {
    if (!this.s.tokens?.isSignedIn()) return [];
    if (n instanceof RunNode) return this.stepsOf(n.run, n.bu);
    const store = this.s.store;
    if (n instanceof BuNode) {
      const d = store.data.get(n.bu); if (!d) return [];
      const runs = [...d.runs].sort((a, b) => rank(a.status) - rank(b.status) || (b.created_at ?? "").localeCompare(a.created_at ?? ""));
      const name = (id: string) => d.workspaces.find((w) => w.id === id)?.name ?? id.slice(0, 8);
      const msgs: Node[] = d.error ? [new MessageNode(`Refresh failed: ${d.error}`, "warning")] : runs.length ? [] : [new MessageNode("No recent runs")];
      return [...msgs, ...runs.map((r) => new RunNode(r, { bu: n.bu, showWorkspace: name(r.workspace_id), collapsible: true }))];
    }
    if (n) return [];
    if (!store.bus.length && !store.globalError) return [new MessageNode("No business units")];
    const visible = store.visibleBus();
    return [...visible].sort(byName).map((bu) => {
      const d = store.data.get(bu.slug);
      return new BuNode(bu, d?.error ? `${bu.slug} · error` : `${bu.slug} · ${plural(d?.runs.length ?? 0, "run")}`, visible.length === 1);
    });
  }

  /** Steps are fetched lazily — only when a run is actually expanded — and without their output
   *  (`include_output=false`), which is what the run's OutputChannel is for. */
  private async stepsOf(run: Run, bu: string): Promise<Node[]> {
    const cached = this.steps.get(run.id);
    if (cached) return cached;
    const client = this.s.client?.withBu(bu);
    if (!client) return [];
    try {
      const steps = await client.getSteps(run.id, 0, false);
      const nodes: Node[] = steps.length
        ? [...steps].sort((a, b) => a.position - b.position).map((st) => new StepNode(run, st))
        : [new MessageNode("No steps yet")];
      if (TERMINAL_RUN_STATUSES.has(run.status)) this.steps.set(run.id, nodes);
      return nodes;
    } catch (e) {
      return [new MessageNode(`Steps unavailable: ${e instanceof Error ? e.message : String(e)}`, "warning")];
    }
  }
}
