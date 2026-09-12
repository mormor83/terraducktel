import * as vscode from "vscode";
import type { Run } from "../api/types";
import { TERMINAL_RUN_STATUSES } from "../api/types";
import type { Session } from "../session";
import { MessageNode, RunNode, StepNode, type Node } from "./nodes";

const ORDER = ["awaiting_approval", "applying", "running", "planning", "pending", "planned", "failed", "applied", "cancelled"];
/** Unknown / future statuses sort after every known one instead of ahead of them (`indexOf` → -1). */
const rank = (status: string) => { const i = ORDER.indexOf(status); return i === -1 ? ORDER.length : i; };

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
    if (n instanceof RunNode) return this.stepsOf(n.run);
    if (n) return [];
    const runs = [...this.s.store.runs].sort((a, b) => rank(a.status) - rank(b.status) || (b.created_at ?? "").localeCompare(a.created_at ?? ""));
    if (!runs.length) return [new MessageNode("No runs yet")];
    const name = (id: string) => this.s.store.workspace(id)?.name ?? id.slice(0, 8);
    return runs.map((r) => new RunNode(r, { showWorkspace: name(r.workspace_id), collapsible: true }));
  }

  /** Steps are fetched lazily — only when a run is actually expanded — and without their output
   *  (`include_output=false`), which is what the run's OutputChannel is for. */
  private async stepsOf(run: Run): Promise<Node[]> {
    const cached = this.steps.get(run.id);
    if (cached) return cached;
    const client = this.s.client;
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
