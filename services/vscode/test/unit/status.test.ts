import { afterEach, describe, expect, it, vi } from "vitest";
import { EditorStatus } from "../../src/editor/status";
import { GitProbe, type ExecFn } from "../../src/editor/git";
import type { PlanDocumentProvider } from "../../src/output/planDocument";
import type { Run, Workspace } from "../../src/api/types";
import type { Session } from "../../src/session";
import * as vscodeStub from "./vscode-stub";

// The test aliases the pure module under `vscode` (see vitest.config.ts) to this same file, so
// reaching into it directly gives us the live `window`/`commands` surfaces EditorStatus talks to.
const stub = vscodeStub as unknown as {
  window: { activeTextEditor: unknown };
  statusBarItems: Array<{ text: string; tooltip: unknown; backgroundColor: unknown; visible: boolean }>;
  setContextCalls: Array<{ key: string; value: unknown }>;
};

const ws = (p: Partial<Workspace> & { name: string }): Workspace => ({
  id: p.name, business_unit_id: "bu", environment: "dev", region: "us-east-1",
  aws_account_id: "1", repo_ref: "main", kind: "terraform", tags: {}, drift_status: "unknown",
  path_status: "ok", state_backend: "s3", tf_working_dir: "account-1/eu-west-1/vpc",
  repo_url: "https://github.com/acme/infra.git", ...p,
});

function fakeSession(opts: { signedIn?: boolean; workspaces?: Workspace[]; runs?: Record<string, Run[]> } = {}): Session {
  const signedIn = opts.signedIn ?? true;
  return {
    tokens: { isSignedIn: () => signedIn },
    store: {
      workspaces: opts.workspaces ?? [],
      runsFor: (id: string) => opts.runs?.[id] ?? [],
      onDidChange: () => ({ dispose() {} }),
    },
    onDidChange: () => ({ dispose() {} }),
    uiUrl: () => "http://ui.example",
  } as unknown as Session;
}

const fakeDoc = (fsPath: string) => ({ uri: { scheme: "file", fsPath }, languageId: "terraform" });

/** Every `EditorStatus` a test has created, so `afterEach` can dispose them all — an undisposed
 *  instance leaves its subscriptions (event listeners, registered commands) live for the next
 *  test in this file, since the stub module (and its `statusBarItems`) is shared across `it`s. */
const createdStatuses: EditorStatus[] = [];

/** A `GitProbe` bound to a scripted `exec`, plus the plumbing EditorStatus needs. */
function make(session: Session, exec: ExecFn) {
  const git = new GitProbe({ exec });
  const plans = { open: vi.fn() } as unknown as PlanDocumentProvider;
  const reveal = vi.fn(async () => {});
  const watch = vi.fn();
  const status = new EditorStatus(session, { watch, plans, reveal, git });
  createdStatuses.push(status);
  const item = stub.statusBarItems[stub.statusBarItems.length - 1];
  return { status, item, plans, reveal, watch };
}

/** Answers `rev-parse --show-toplevel` / `remote get-url origin` / `rev-parse --abbrev-ref HEAD`. */
const gitExec = (root: string, remote: string | undefined, branch: string | undefined): ExecFn => async (_cmd, args) => {
  if (args[0] === "rev-parse" && args[1] === "--show-toplevel") return `${root}\n`;
  if (args[0] === "remote") { if (!remote) throw new Error("no origin"); return `${remote}\n`; }
  return `${branch ?? "HEAD"}\n`; // rev-parse --abbrev-ref HEAD
};

describe("EditorStatus", () => {
  afterEach(() => {
    stub.window.activeTextEditor = undefined; stub.setContextCalls.length = 0;
    for (const s of createdStatuses.splice(0)) s.dispose();
    stub.statusBarItems.length = 0;
  });

  it("shows the mapped workspace's text and tooltip", async () => {
    const w = ws({ name: "vpc" });
    const session = fakeSession({ workspaces: [w], runs: { vpc: [{ id: "r1", workspace_id: "vpc", command: "plan", status: "planned", created_at: "t" } as Run] } });
    stub.window.activeTextEditor = { document: fakeDoc("/repo/account-1/eu-west-1/vpc/main.tf") };
    const { status, item } = make(session, gitExec("/repo", "https://github.com/acme/infra.git", "main"));
    await status.refresh();
    expect(item.text).toBe("$(cloud) TDT: vpc · planned");
    expect(item.tooltip).toContain("account-1/eu-west-1/vpc");
    expect(item.visible).toBe(true);
    expect(stub.setContextCalls.at(-1)).toEqual({ key: "terraducktel.currentFileMapped", value: true });
  });

  it("colours the item for a failed run and a run awaiting approval, default otherwise", async () => {
    const w = ws({ name: "vpc" });
    const exec = gitExec("/repo", "https://github.com/acme/infra.git", "main");
    stub.window.activeTextEditor = { document: fakeDoc("/repo/account-1/eu-west-1/vpc/main.tf") };

    const failed = fakeSession({ workspaces: [w], runs: { vpc: [{ id: "r1", workspace_id: "vpc", command: "apply", status: "failed", created_at: "t" } as Run] } });
    const a = make(failed, exec); await a.status.refresh();
    expect((a.item.backgroundColor as { id: string } | undefined)?.id).toBe("statusBarItem.errorBackground");

    const awaiting = fakeSession({ workspaces: [w], runs: { vpc: [{ id: "r2", workspace_id: "vpc", command: "apply", status: "awaiting_approval", created_at: "t" } as Run] } });
    const b = make(awaiting, exec); await b.status.refresh();
    expect((b.item.backgroundColor as { id: string } | undefined)?.id).toBe("statusBarItem.warningBackground");

    const planned = fakeSession({ workspaces: [w], runs: { vpc: [{ id: "r3", workspace_id: "vpc", command: "plan", status: "planned", created_at: "t" } as Run] } });
    const c = make(planned, exec); await c.status.refresh();
    expect(c.item.backgroundColor).toBeUndefined();
  });

  it("hides the item when signed out and records setContext(currentFileMapped, false)", async () => {
    const session = fakeSession({ signedIn: false, workspaces: [ws({ name: "vpc" })] });
    stub.window.activeTextEditor = { document: fakeDoc("/repo/account-1/eu-west-1/vpc/main.tf") };
    const { status, item } = make(session, gitExec("/repo", "https://github.com/acme/infra.git", "main"));
    await status.refresh();
    expect(item.visible).toBe(false);
    expect(status.current()).toBeUndefined();
    expect(stub.setContextCalls.at(-1)).toEqual({ key: "terraducktel.currentFileMapped", value: false });
  });

  it("shows 'not imported' when no workspace covers the file", async () => {
    const session = fakeSession({ workspaces: [ws({ name: "vpc", tf_working_dir: "account-1/eu-west-1/other" })] });
    stub.window.activeTextEditor = { document: fakeDoc("/repo/account-1/eu-west-1/vpc/main.tf") };
    const { status, item } = make(session, gitExec("/repo", "https://github.com/acme/infra.git", "main"));
    await status.refresh();
    expect(item.text).toBe("$(cloud) TDT: not imported");
    expect(item.visible).toBe(true);
    expect(status.current()).toBeUndefined();
  });

  it("does not let a slow, superseded refresh() overwrite a newer result", async () => {
    const a = ws({ name: "a", tf_working_dir: "account-1/eu-west-1/vpc" });
    const b = ws({ name: "b", tf_working_dir: "account-1/eu-west-1/vpc2" });
    const session = fakeSession({ workspaces: [a, b] });
    let releaseA!: () => void;
    const gateA = new Promise<void>((r) => { releaseA = r; });
    const exec: ExecFn = async (_cmd, args, cwd) => {
      if (args[0] === "rev-parse" && args[1] === "--show-toplevel") {
        if (cwd === "/repo/account-1/eu-west-1/vpc") await gateA;   // block only A's probe
        return "/repo\n";
      }
      if (args[0] === "remote") return "https://github.com/acme/infra.git\n";
      return "main\n";
    };
    const { status } = make(session, exec);

    stub.window.activeTextEditor = { document: fakeDoc("/repo/account-1/eu-west-1/vpc/main.tf") };
    const p1 = status.refresh();                          // seq=1, will hang on gateA
    await new Promise((r) => setTimeout(r, 20));           // let p1 pass resolvePath and reach the (blocked) probe
    stub.window.activeTextEditor = { document: fakeDoc("/repo/account-1/eu-west-1/vpc2/main.tf") };
    const p2 = status.refresh();                          // seq=2, unblocked
    await p2;
    expect(status.current()?.ws.name).toBe("b");
    releaseA();
    await p1;                                              // the stale refresh finally lands…
    expect(status.current()?.ws.name).toBe("b");           // …but must not have overwritten "b"
  });

  it("re-probes the branch right before pinning, not the stale one from the last refresh()", async () => {
    const w = ws({ name: "vpc" });
    let branch = "main";
    // rev-parse --show-toplevel / remote get-url origin / rev-parse --abbrev-ref HEAD
    const exec: ExecFn = async (_cmd, args) => {
      if (args[0] === "rev-parse" && args[1] === "--show-toplevel") return "/repo\n";
      if (args[0] === "remote") return "https://github.com/acme/infra.git\n";
      return `${branch}\n`;
    };
    const run = { id: "r1", workspace_id: "vpc", command: "plan", status: "planning", created_at: "t" } as Run;
    const updateWorkspace = vi.fn(async () => undefined);
    const triggerRun = vi.fn(async () => run);
    const refresh = vi.fn(async () => undefined);
    const session = {
      tokens: { isSignedIn: () => true },
      store: { workspaces: [w], runsFor: () => [], onDidChange: () => ({ dispose() {} }), refresh },
      onDidChange: () => ({ dispose() {} }),
      uiUrl: () => "http://ui.example",
      requireClient: () => ({ updateWorkspace, triggerRun }),
    } as unknown as Session;

    // Capture the command handlers EditorStatus registers, the way the real extension host
    // would dispatch them, without disturbing the shared stub for other tests in this file.
    const registerSpy = vi.spyOn(vscodeStub.commands, "registerCommand");
    stub.window.activeTextEditor = { document: fakeDoc("/repo/account-1/eu-west-1/vpc/main.tf") };
    const { status } = make(session, exec);
    await status.refresh();                                // caches git.branch = "main", same as ws.repo_ref

    branch = "feat/y";                                      // the working tree moves on after the refresh
    const originalShowQuickPick = vscodeStub.window.showQuickPick;
    vscodeStub.window.showQuickPick = (async (items: Array<{ b?: string }>) => items[0]) as typeof vscodeStub.window.showQuickPick;

    const planHandler = registerSpy.mock.calls.find(([id]) => id === "terraducktel.planCurrentFile")?.[1] as (() => Promise<void>) | undefined;
    expect(planHandler).toBeDefined();
    await planHandler!();

    expect(updateWorkspace).toHaveBeenCalledWith("vpc", { repo_ref: "feat/y" }); // re-probed branch, not the stale "main"
    expect(triggerRun).toHaveBeenCalledWith("vpc", { command: "plan" });

    vscodeStub.window.showQuickPick = originalShowQuickPick;
    registerSpy.mockRestore();
  });
});
