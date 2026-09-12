# VS Code Extension — Milestone B Implementation Plan (editor ↔ workspace mapping)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a Terraform file is open, the extension resolves which TDT workspace it belongs to, shows it in the status bar with the last-run status, and offers "Plan this leaf" (with branch awareness), "Show last plan", "Reveal in sidebar" and "Open in browser"; unmatched files show "TDT: not imported".

**Architecture:** Two pure modules (`editor/mapping.ts` for repo-URL normalisation + longest-prefix matching, `editor/git.ts` for a cached, never-throwing git probe via `child_process.execFile`) feed an `EditorStatus` controller that owns one `StatusBarItem` and reacts to active-editor, store and session changes. Actions reuse the milestone-A command internals (a `runCommandFor()` helper extracted from `commands/workspace.ts`). "Reveal in sidebar" adds an id-keyed node cache + `getParent` to `WorkspacesTree` so `TreeView.reveal()` works.

**Tech Stack:** as milestone A (TypeScript strict, esbuild, vitest, Node built-ins only).

**Spec:** `docs/superpowers/specs/2026-09-12-vscode-extension-design.md` §5 (+ §7–§9)

## Global Constraints

- Same as milestone A: no runtime deps; secrets never logged; command ids prefixed `terraducktel.`; commit trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` then `Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit`; commands run from `services/vscode` inside the `vscode-extension` worktree (`npm test`, `npm run typecheck`, `npm run build`, `npm run test:integration`).
- Mapping rule (spec §5): a file maps to the workspace whose `repo_url` matches the enclosing git checkout's `origin` (host + path, scheme/user/`.git`/trailing-slash-insensitive, host case-insensitive) **and** whose `tf_working_dir` is the longest prefix of the file's directory relative to the git root. `local://` workspaces match by `tf_working_dir` alone. When the remote is unknown (no git, no origin) fall back to a path-only match only if it is unique.
- Branch awareness: if the checkout's current branch ≠ `workspace.repo_ref`, "Plan this leaf" offers `Plan on <branch> (pins the workspace)` vs `Plan on <repo_ref>`; pinning is `PUT /workspaces/{id} {repo_ref}` (already in the contract).
- The git probe never throws and never blocks the UI: `execFile` with a 3 s timeout, results cached per git root for 10 s, a failing probe yields `undefined`.
- New settings: `terraducktel.statusBar.enabled` (bool, default true). New commands: `terraducktel.currentFileActions`, `terraducktel.planCurrentFile`, `terraducktel.revealCurrentWorkspace` (palette-gated on `terraducktel.signedIn`; `currentFileActions` additionally `when: terraducktel.currentFileMapped`). New context key `terraducktel.currentFileMapped`.
- Version bump `0.1.0` → `0.2.0` in `services/vscode/package.json` (last task).

## File structure

| File | Responsibility |
|---|---|
| `src/editor/mapping.ts` | `normalizeRepoUrl`, `relativeDir`, `matchWorkspace` — pure |
| `src/editor/git.ts` | `GitProbe` (root, origin URL, branch) with cache + timeout |
| `src/editor/status.ts` | `EditorStatus`: status bar item, context key, quick-pick actions |
| `src/commands/workspace.ts` | extract `runCommandFor(session, ws, command, watch, opts)` (used by existing commands and by the status bar) |
| `src/views/workspacesTree.ts` | id-keyed node cache, `getParent`, `nodeForWorkspace(id)` |
| `src/extension.ts` | wire `EditorStatus`, pass `wsView` for reveal |
| `package.json` | commands, palette `when`, setting, version |
| `test/unit/mapping.test.ts`, `test/unit/git.test.ts` | unit tests (git test uses a real temp repo) |
| `docs/VSCODE.md`, spec | docs |

---

### Task 1: Pure mapping module

**Files:**
- Create: `services/vscode/src/editor/mapping.ts`, `services/vscode/test/unit/mapping.test.ts`

**Interfaces:**
- Produces: `normalizeRepoUrl(url: string | null | undefined): string | undefined` (e.g. `https://github.com/Org/Repo.git` → `github.com/org/repo`; `git@github.com:Org/Repo.git` → `github.com/org/repo`; `http://forgejo:3002/infra/live/` → `forgejo:3002/infra/live`; `local:///mnt/repos/x` → `local:/mnt/repos/x`); `relativeDir(gitRoot: string, filePath: string): string | undefined` (posix, `""` for the root, `undefined` when outside); `matchWorkspace(workspaces: Workspace[], q: { relativeDir: string; remoteUrl?: string }): { ws: Workspace; exact: boolean } | undefined`.

- [ ] **Step 1: Failing tests**

```ts
// services/vscode/test/unit/mapping.test.ts
import { describe, expect, it } from "vitest";
import { matchWorkspace, normalizeRepoUrl, relativeDir } from "../../src/editor/mapping";
import type { Workspace } from "../../src/api/types";

const ws = (p: Partial<Workspace> & { name: string; tf_working_dir: string }): Workspace => ({
  id: p.name, business_unit_id: "bu", environment: "dev", region: "us-east-1", aws_account_id: "1", repo_ref: "main",
  kind: "terraform", tags: {}, drift_status: "unknown", path_status: "ok", state_backend: "s3", repo_url: "https://github.com/acme/infra.git", ...p,
});

describe("normalizeRepoUrl", () => {
  it.each([
    ["https://github.com/Acme/Infra.git", "github.com/acme/infra"],
    ["https://github.com/acme/infra", "github.com/acme/infra"],
    ["git@github.com:acme/infra.git", "github.com/acme/infra"],
    ["ssh://git@github.com/acme/infra.git", "github.com/acme/infra"],
    ["http://forgejo:3002/infra/live/", "forgejo:3002/infra/live"],
    ["https://user:tok@gitlab.example.com/grp/sub/repo.git", "gitlab.example.com/grp/sub/repo"],
    ["local:///mnt/local-repos/probe", "local:/mnt/local-repos/probe"],
  ])("%s → %s", (input, want) => expect(normalizeRepoUrl(input)).toBe(want));
  it("returns undefined for empty / garbage", () => {
    expect(normalizeRepoUrl("")).toBeUndefined(); expect(normalizeRepoUrl(null)).toBeUndefined(); expect(normalizeRepoUrl("not a url")).toBeUndefined();
  });
});

describe("relativeDir", () => {
  it("returns the posix directory relative to the root", () => {
    expect(relativeDir("/home/u/infra", "/home/u/infra/account-1/eu-west-1/vpc/main.tf")).toBe("account-1/eu-west-1/vpc");
    expect(relativeDir("/home/u/infra", "/home/u/infra/main.tf")).toBe("");
  });
  it("returns undefined for files outside the root", () => {
    expect(relativeDir("/home/u/infra", "/home/u/other/main.tf")).toBeUndefined();
    expect(relativeDir("/home/u/infra", "/home/u/infra2/main.tf")).toBeUndefined();
  });
});

describe("matchWorkspace", () => {
  const list = [
    ws({ name: "vpc", tf_working_dir: "account-1/eu-west-1/vpc" }),
    ws({ name: "vpc-peering", tf_working_dir: "account-1/eu-west-1/vpc/peering" }),
    ws({ name: "other-repo", tf_working_dir: "account-1/eu-west-1/vpc", repo_url: "https://github.com/acme/other.git" }),
    ws({ name: "local", tf_working_dir: "proxmox/cluster-home/pve/probe", repo_url: "local:///mnt/local-repos/probe" }),
  ];
  it("picks the longest tf_working_dir prefix within the matching repo", () => {
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpc/peering/modules", remoteUrl: "git@github.com:acme/infra.git" })?.ws.name).toBe("vpc-peering");
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpc", remoteUrl: "https://github.com/acme/infra" })).toMatchObject({ ws: { name: "vpc" }, exact: true });
  });
  it("excludes workspaces from a different repo", () => {
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpc", remoteUrl: "https://github.com/acme/other.git" })?.ws.name).toBe("other-repo");
  });
  it("matches local:// workspaces by path alone", () => {
    expect(matchWorkspace(list, { relativeDir: "proxmox/cluster-home/pve/probe" })?.ws.name).toBe("local");
    expect(matchWorkspace(list, { relativeDir: "proxmox/cluster-home/pve/probe", remoteUrl: "https://github.com/acme/infra.git" })?.ws.name).toBe("local");
  });
  it("with an unknown remote falls back to a path match only when unique", () => {
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpc/peering" })?.ws.name).toBe("vpc-peering"); // unique longest
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpc" })).toBeUndefined();               // vpc vs other-repo tie
  });
  it("does not match a parent directory or an unrelated path", () => {
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1", remoteUrl: "https://github.com/acme/infra.git" })).toBeUndefined();
    expect(matchWorkspace(list, { relativeDir: "account-1/eu-west-1/vpcx", remoteUrl: "https://github.com/acme/infra.git" })).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run** `npm test` → module not found.

- [ ] **Step 3: Implement**

```ts
// services/vscode/src/editor/mapping.ts
import * as path from "node:path";
import type { Workspace } from "../api/types";

/** Canonical "host/path" form for comparing git remotes across schemes. `local://` keeps its path. */
export function normalizeRepoUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  let u = url.trim();
  if (!u) return undefined;
  if (u.startsWith("local://")) { const p = u.slice("local://".length).replace(/\/+$/, ""); return p ? `local:${p}` : undefined; }
  // scp-like: git@host:org/repo(.git)
  const scp = u.match(/^(?:[\w.-]+@)?([\w.-]+):(?!\/\/)([^\s]+)$/);
  if (scp) u = `ssh://${scp[1]}/${scp[2]}`;
  let host: string, p: string;
  try { const parsed = new URL(u); host = parsed.host.toLowerCase(); p = parsed.pathname; } catch { return undefined; }
  if (!host) return undefined;
  p = p.replace(/\/+$/, "").replace(/\.git$/i, "").replace(/^\/+/, "");
  if (!p) return undefined;
  return `${host}/${p}`;
}

/** Directory of `filePath` relative to `gitRoot`, posix-separated; "" at the root; undefined when outside. */
export function relativeDir(gitRoot: string, filePath: string): string | undefined {
  const rel = path.relative(gitRoot, path.dirname(filePath));
  if (rel.startsWith("..") || path.isAbsolute(rel)) return undefined;
  return rel.split(path.sep).join("/");
}

const isPrefix = (dir: string, wd: string) => dir === wd || dir.startsWith(wd.replace(/\/+$/, "") + "/");

/** Longest `tf_working_dir` prefix among workspaces whose repo matches; see Global Constraints for the fallback rules. */
export function matchWorkspace(workspaces: Workspace[], q: { relativeDir: string; remoteUrl?: string }): { ws: Workspace; exact: boolean } | undefined {
  const remote = normalizeRepoUrl(q.remoteUrl);
  const candidates = workspaces.filter((w) => {
    const wd = (w.tf_working_dir ?? "").replace(/^\/+|\/+$/g, "");
    if (!wd || wd === "." || !isPrefix(q.relativeDir, wd)) return false;
    const wsRepo = normalizeRepoUrl(w.repo_url);
    if (wsRepo?.startsWith("local:")) return true;            // local checkouts match by path alone
    if (remote) return wsRepo === remote;                      // known remote: must match
    return true;                                               // unknown remote: path only, resolved below
  });
  if (!candidates.length) return undefined;
  const longest = Math.max(...candidates.map((w) => w.tf_working_dir.length));
  const best = candidates.filter((w) => w.tf_working_dir.length === longest);
  if (best.length !== 1) return undefined;                     // ambiguous (typically unknown remote + same path in two repos)
  const ws = best[0];
  return { ws, exact: ws.tf_working_dir.replace(/^\/+|\/+$/g, "") === q.relativeDir };
}
```

- [ ] **Step 4: Run** `npm test` → green; `npm run typecheck`.
- [ ] **Step 5: Commit** — `feat(vscode): pure editor→workspace mapping (repo URL normalisation, longest-prefix match)` + trailers.

---

### Task 2: Git probe

**Files:**
- Create: `services/vscode/src/editor/git.ts`, `services/vscode/test/unit/git.test.ts`

**Interfaces:**
- Produces: `class GitProbe { constructor(opts?: { ttlMs?: number; timeoutMs?: number; exec?: ExecFn }); async info(filePath: string): Promise<GitInfo | undefined>; invalidate(root?: string): void }` with `GitInfo = { root: string; remoteUrl?: string; branch?: string }`; `ExecFn = (cmd: string, args: string[], cwd: string, timeoutMs: number) => Promise<string>` (default wraps `child_process.execFile`).

- [ ] **Step 1: Failing tests** (real git in a temp dir; skipped if `git` is missing)

```ts
// services/vscode/test/unit/git.test.ts
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import * as path from "node:path";
import { GitProbe } from "../../src/editor/git";

const haveGit = (() => { try { execFileSync("git", ["--version"], { stdio: "ignore" }); return true; } catch { return false; } })();
const d = describe.skipIf(!haveGit);

d("GitProbe", () => {
  let root: string; let file: string;
  beforeAll(() => {
    root = mkdtempSync(path.join(tmpdir(), "tdt-git-"));
    const g = (...a: string[]) => execFileSync("git", a, { cwd: root, stdio: "ignore" });
    g("init", "-q", "-b", "feat/x"); g("remote", "add", "origin", "git@github.com:acme/infra.git");
    mkdirSync(path.join(root, "account-1/eu-west-1/vpc"), { recursive: true });
    file = path.join(root, "account-1/eu-west-1/vpc/main.tf"); writeFileSync(file, "# tf\n");
    g("add", "-A"); execFileSync("git", ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], { cwd: root, stdio: "ignore" });
  });
  afterAll(() => rmSync(root, { recursive: true, force: true }));

  it("reports root, origin and branch for a file inside a checkout", async () => {
    const info = await new GitProbe().info(file);
    expect(info?.root && path.resolve(info.root)).toBe(path.resolve(root));   // macOS tmp symlinks
    expect(info?.remoteUrl).toBe("git@github.com:acme/infra.git");
    expect(info?.branch).toBe("feat/x");
  });
  it("returns undefined outside any checkout and never throws", async () => {
    const outside = mkdtempSync(path.join(tmpdir(), "tdt-nogit-"));
    try { expect(await new GitProbe().info(path.join(outside, "x.tf"))).toBeUndefined(); } finally { rmSync(outside, { recursive: true, force: true }); }
  });
  it("caches per root within the TTL and invalidates on demand", async () => {
    let calls = 0;
    const exec = async (cmd: string, args: string[], cwd: string, t: number) => { calls++; return new GitProbe({}).constructor === GitProbe ? (await import("../../src/editor/git")).defaultExec(cmd, args, cwd, t) : ""; };
    const p = new GitProbe({ ttlMs: 10_000, exec });
    await p.info(file); const first = calls; await p.info(file); expect(calls).toBe(first);
    p.invalidate(); await p.info(file); expect(calls).toBeGreaterThan(first);
  });
  it("treats a missing origin as remoteUrl undefined", async () => {
    execFileSync("git", ["remote", "remove", "origin"], { cwd: root, stdio: "ignore" });
    const info = await new GitProbe().info(file);
    expect(info?.remoteUrl).toBeUndefined(); expect(info?.branch).toBe("feat/x");
  });
});
```

- [ ] **Step 2: Run** → module not found.

- [ ] **Step 3: Implement**

```ts
// services/vscode/src/editor/git.ts
import { execFile } from "node:child_process";
import * as path from "node:path";

export interface GitInfo { root: string; remoteUrl?: string; branch?: string }
export type ExecFn = (cmd: string, args: string[], cwd: string, timeoutMs: number) => Promise<string>;

export const defaultExec: ExecFn = (cmd, args, cwd, timeoutMs) =>
  new Promise((resolve, reject) => {
    execFile(cmd, args, { cwd, timeout: timeoutMs, windowsHide: true, maxBuffer: 1 << 20 }, (err, stdout) => (err ? reject(err) : resolve(String(stdout))));
  });

/** Cheap, cached, never-throwing view of the git checkout a file lives in. */
export class GitProbe {
  private cache = new Map<string, { at: number; info: GitInfo }>();   // by root
  private rootByDir = new Map<string, { at: number; root: string | undefined }>();
  private readonly ttl: number; private readonly timeout: number; private readonly exec: ExecFn;
  constructor(opts: { ttlMs?: number; timeoutMs?: number; exec?: ExecFn } = {}) {
    this.ttl = opts.ttlMs ?? 10_000; this.timeout = opts.timeoutMs ?? 3_000; this.exec = opts.exec ?? defaultExec;
  }
  invalidate(root?: string) { if (root) { this.cache.delete(root); } else { this.cache.clear(); } this.rootByDir.clear(); }

  async info(filePath: string): Promise<GitInfo | undefined> {
    const dir = path.dirname(filePath); const now = Date.now();
    let root: string | undefined;
    const rc = this.rootByDir.get(dir);
    if (rc && now - rc.at < this.ttl) root = rc.root;
    else {
      root = await this.exec("git", ["rev-parse", "--show-toplevel"], dir, this.timeout).then((s) => s.trim() || undefined, () => undefined);
      this.rootByDir.set(dir, { at: now, root });
    }
    if (!root) return undefined;
    const hit = this.cache.get(root);
    if (hit && now - hit.at < this.ttl) return hit.info;
    const [remoteUrl, branch] = await Promise.all([
      this.exec("git", ["remote", "get-url", "origin"], root, this.timeout).then((s) => s.trim() || undefined, () => undefined),
      this.exec("git", ["rev-parse", "--abbrev-ref", "HEAD"], root, this.timeout).then((s) => { const b = s.trim(); return b && b !== "HEAD" ? b : undefined; }, () => undefined),
    ]);
    const info: GitInfo = { root, remoteUrl, branch };
    this.cache.set(root, { at: now, info });
    return info;
  }
}
```

Note for the implementer: the cache test above is deliberately simple — if the `exec` wrapper indirection reads awkwardly, replace it with a counting wrapper around `defaultExec` (`const exec: ExecFn = (...a) => { calls++; return defaultExec(...a); }`). The assertion that matters: second `info()` within the TTL makes no exec calls; `invalidate()` forces new calls.

- [ ] **Step 4: Run** tests (twice — they touch the filesystem), typecheck.
- [ ] **Step 5: Commit** — `feat(vscode): cached git probe for the active file (root, origin, branch)` + trailers.

---

### Task 3: Status bar, current-file actions, command extraction

**Files:**
- Create: `services/vscode/src/editor/status.ts`
- Modify: `services/vscode/src/commands/workspace.ts` (extract `runCommandFor`), `src/ids.ts` (`CTX_FILE_MAPPED = "terraducktel.currentFileMapped"`), `src/extension.ts`, `package.json` (commands, palette `when`, setting)

**Interfaces:**
- Consumes: `matchWorkspace`, `relativeDir` (T1), `GitProbe` (T2), `Session`, `Store`, `RunOutputManager.watch` via the `watch` function, `PlanDocumentProvider.open`.
- Produces: `runCommandFor(s: Session, ws: Workspace, command: "plan"|"apply"|"destroy", watch: (r: Run) => void, opts?: { branch?: string }): Promise<void>` (exported from `commands/workspace.ts`; when `opts.branch` is set and differs from `ws.repo_ref`, PUT `{repo_ref: branch}` first, then trigger); `class EditorStatus implements vscode.Disposable` with `constructor(s: Session, deps: { watch, plans, reveal: (wsId: string) => Promise<void> })`, `current(): { ws: Workspace; git?: GitInfo; exact: boolean } | undefined`, `refresh(): Promise<void>`.

- [ ] **Step 1: Extract `runCommandFor` (behaviour-preserving)**

In `commands/workspace.ts`, move the body of the local `trigger()` into an exported `runCommandFor(s, ws, command, watch, opts = {})`: the Apply modal and Destroy name-typing guards stay; before `triggerRun`, `if (opts.branch && opts.branch !== ws.repo_ref) await c.updateWorkspace(ws.id, { repo_ref: opts.branch })`; the command registrations call `runCommandFor(s, ws, command, watch)`. `npm test` + typecheck stay green (no unit tests cover this file; typecheck is the gate).

- [ ] **Step 2: `ids.ts`** — add `export const CTX_FILE_MAPPED = "terraducktel.currentFileMapped";`

- [ ] **Step 3: `editor/status.ts`**

```ts
// services/vscode/src/editor/status.ts
import * as vscode from "vscode";
import type { Session } from "../session";
import type { Run, Workspace } from "../api/types";
import { GitProbe, type GitInfo } from "./git";
import { matchWorkspace, relativeDir } from "./mapping";
import { runCommandFor } from "../commands/workspace";
import type { PlanDocumentProvider } from "../output/planDocument";
import { CTX_FILE_MAPPED } from "../ids";

const TF_LANGS = new Set(["terraform", "terraform-vars", "hcl"]);
const isTfFile = (doc: vscode.TextDocument) => doc.uri.scheme === "file" && (TF_LANGS.has(doc.languageId) || /\.(tf|tfvars|hcl)$/i.test(doc.uri.fsPath));

export interface CurrentFile { ws: Workspace; git?: GitInfo; exact: boolean }

/** One status-bar item that says which TDT workspace the active Terraform file belongs to. */
export class EditorStatus implements vscode.Disposable {
  private item: vscode.StatusBarItem;
  private git = new GitProbe();
  private cur: CurrentFile | undefined;
  private lastFile: string | undefined;
  private subs: vscode.Disposable[] = [];
  private seq = 0;

  constructor(private readonly s: Session, private readonly deps: { watch: (r: Run) => void; plans: PlanDocumentProvider; reveal: (wsId: string) => Promise<void> }) {
    this.item = vscode.window.createStatusBarItem("terraducktel.currentFile", vscode.StatusBarAlignment.Left, 50);
    this.item.name = "Terraducktel workspace"; this.item.command = "terraducktel.currentFileActions";
    this.subs.push(this.item,
      vscode.window.onDidChangeActiveTextEditor(() => void this.refresh()),
      vscode.workspace.onDidSaveTextDocument(() => this.git.invalidate()),           // branch may have changed via a commit
      s.store.onDidChange(() => void this.refresh()), s.onDidChange(() => void this.refresh()),
      vscode.workspace.onDidChangeConfiguration((e) => { if (e.affectsConfiguration("terraducktel.statusBar")) void this.refresh(); }),
      vscode.commands.registerCommand("terraducktel.currentFileActions", () => this.actions()),
      vscode.commands.registerCommand("terraducktel.planCurrentFile", () => this.planCurrent()),
      vscode.commands.registerCommand("terraducktel.revealCurrentWorkspace", () => this.cur && this.deps.reveal(this.cur.ws.id)),
    );
    void this.refresh();
  }
  current() { return this.cur; }

  async refresh(): Promise<void> {
    const my = ++this.seq;
    const enabled = vscode.workspace.getConfiguration("terraducktel").get<boolean>("statusBar.enabled", true);
    const doc = vscode.window.activeTextEditor?.document;
    if (!enabled || !doc || !isTfFile(doc) || !this.s.tokens?.isSignedIn()) { this.set(undefined, undefined, false); return; }
    const git = await this.git.info(doc.uri.fsPath);
    if (my !== this.seq) return;                                            // a newer refresh superseded this one
    let match: ReturnType<typeof matchWorkspace>;
    if (git) { const rel = relativeDir(git.root, doc.uri.fsPath); if (rel !== undefined) match = matchWorkspace(this.s.store.workspaces, { relativeDir: rel, remoteUrl: git.remoteUrl }); }
    this.lastFile = doc.uri.fsPath;
    this.set(match ? { ws: match.ws, git, exact: match.exact } : undefined, git, true);
  }

  private set(cur: CurrentFile | undefined, git: GitInfo | undefined, showUnmapped: boolean) {
    this.cur = cur;
    void vscode.commands.executeCommand("setContext", CTX_FILE_MAPPED, !!cur);
    if (cur) {
      const last = this.s.store.runsFor(cur.ws.id)[0];
      const branchNote = git?.branch && git.branch !== cur.ws.repo_ref ? ` · on ${git.branch} (tracks ${cur.ws.repo_ref})` : "";
      this.item.text = `$(cloud) TDT: ${cur.ws.name}${last ? ` · ${last.status}` : ""}`;
      this.item.tooltip = `${cur.ws.tf_working_dir}${cur.exact ? "" : " (parent leaf)"}${branchNote}\nClick for actions`;
      this.item.backgroundColor = last?.status === "failed" ? new vscode.ThemeColor("statusBarItem.errorBackground") : last?.status === "awaiting_approval" ? new vscode.ThemeColor("statusBarItem.warningBackground") : undefined;
      this.item.show();
    } else if (showUnmapped) {
      this.item.text = "$(cloud) TDT: not imported"; this.item.tooltip = git ? "No Terraducktel workspace covers this path. Click to open Discover in the browser." : "Not inside a git checkout Terraducktel knows about."; this.item.backgroundColor = undefined; this.item.show();
    } else this.item.hide();
  }

  private async actions() {
    if (!this.cur) { const ui = this.s.uiUrl(); if (ui) await vscode.env.openExternal(vscode.Uri.parse(`${ui}/`)); return; }
    const { ws, git } = this.cur; const last = this.s.store.runsFor(ws.id)[0];
    type Item = vscode.QuickPickItem & { act: () => Promise<unknown> };
    const items: Item[] = [
      { label: "$(play) Plan this leaf", description: ws.name, act: () => this.planCurrent() },
      ...(last ? [{ label: "$(diff) Show last plan", description: `${last.command} · ${last.status}`, act: () => this.deps.plans.open(last.id, ws.name) }] : []),
      { label: "$(list-tree) Reveal in sidebar", act: () => this.deps.reveal(ws.id) },
      { label: "$(link-external) Open in browser", act: async () => { const ui = this.s.uiUrl(); if (ui) await vscode.env.openExternal(vscode.Uri.parse(`${ui}/`)); } },
    ];
    const pick = await vscode.window.showQuickPick(items, { placeHolder: `${ws.name} · ${ws.tf_working_dir}${git?.branch ? ` · branch ${git.branch}` : ""}` });
    if (pick) await pick.act().catch((e) => vscode.window.showErrorMessage(`Terraducktel: ${e instanceof Error ? e.message : String(e)}`));
  }

  private async planCurrent() {
    if (!this.cur) { void vscode.window.showInformationMessage("Terraducktel: the active file is not inside an imported workspace."); return; }
    const { ws, git } = this.cur;
    let branch: string | undefined;
    if (git?.branch && git.branch !== ws.repo_ref) {
      const pick = await vscode.window.showQuickPick([
        { label: `Plan on ${git.branch}`, description: "pins the workspace to this branch", b: git.branch },
        { label: `Plan on ${ws.repo_ref}`, description: "the workspace's tracked branch", b: undefined as string | undefined },
      ], { placeHolder: `Checked out ${git.branch}, workspace tracks ${ws.repo_ref}` });
      if (!pick) return; branch = pick.b;
    }
    await runCommandFor(this.s, ws, "plan", this.deps.watch, { branch });
  }
  dispose() { for (const d of this.subs) d.dispose(); }
}
```

- [ ] **Step 4: Wire in `extension.ts`** after `registerWorkspaceCommands(...)`:

```ts
  const status = new EditorStatus(session, { watch, plans, reveal: (id) => wsTree.revealWorkspace(wsView, id) });
  context.subscriptions.push(status);
```
(`revealWorkspace` lands in Task 4; until then stub it as `async () => { await vscode.commands.executeCommand("terraducktel.workspaces.focus"); }` and replace in Task 4.)

- [ ] **Step 5: Manifest** — add commands `terraducktel.currentFileActions` ("Current file: actions…"), `terraducktel.planCurrentFile` ("Plan this leaf", icon `$(play)`), `terraducktel.revealCurrentWorkspace` ("Reveal current workspace in sidebar"); `commandPalette` entries: `currentFileActions` + `revealCurrentWorkspace` `when: terraducktel.signedIn && terraducktel.currentFileMapped`, `planCurrentFile` `when: terraducktel.signedIn`; setting `terraducktel.statusBar.enabled` (boolean, default true, "Show the current file's Terraducktel workspace in the status bar."). Optional editor title menu: `editor/title` → `terraducktel.planCurrentFile` `when: terraducktel.currentFileMapped && terraducktel.canWrite`, group `navigation`.

- [ ] **Step 6: Verify** `npm run typecheck && npm test && npm run build`. Manual: F5 Extension Development Host → open a `.tf` inside a repo that has an imported workspace → status bar shows the workspace; click → quick pick works.

- [ ] **Step 7: Commit** — `feat(vscode): status-bar workspace for the active Terraform file, plan-this-leaf with branch pinning` + trailers.

---

### Task 4: Reveal in sidebar (node cache + getParent)

**Files:**
- Modify: `services/vscode/src/views/workspacesTree.ts`, `src/extension.ts` (replace the reveal stub)
- Test: `services/vscode/test/unit/workspacesTree.test.ts` (new; the provider is constructible under the vscode stub — extend the stub with `TreeItemCollapsibleState` already present; add a minimal `TreeView` shape only inside the test)

**Interfaces:**
- Produces: `WorkspacesTree.getParent(n: Node): Node | undefined`; `WorkspacesTree.revealWorkspace(view: vscode.TreeView<Node>, wsId: string): Promise<void>`; getChildren returns cached node instances keyed by `id` (cache cleared when the store or session changes).

- [ ] **Step 1: Failing test**

```ts
// services/vscode/test/unit/workspacesTree.test.ts
import { describe, expect, it } from "vitest";
import { WorkspacesTree } from "../../src/views/workspacesTree";
import type { Workspace } from "../../src/api/types";
import { CloudNode, RegionNode, WorkspaceNode } from "../../src/views/nodes";

const ws = (name: string, dir: string): Workspace => ({ id: name, business_unit_id: "bu", name, environment: "dev", region: "eu-west-1", aws_account_id: "1", tf_working_dir: dir, repo_ref: "main", kind: "terraform", tags: {}, drift_status: "unknown", path_status: "ok", state_backend: "s3" });
function fakeSession(list: Workspace[]) {
  const listeners: Array<() => void> = [];
  return {
    profile: { name: "p", url: "http://x" }, tokens: { isSignedIn: () => true }, bu: "default",
    store: { workspaces: list, runsFor: () => [], lastError: undefined, onDidChange: (l: () => void) => { listeners.push(l); return { dispose() {} }; } },
    onDidChange: () => ({ dispose() {} }), fire: () => listeners.forEach((l) => l()),
  } as never;
}

describe("WorkspacesTree parent chain", () => {
  it("returns stable instances and walks parents up to the cloud group", () => {
    const s = fakeSession([ws("vpc", "account-1/eu-west-1/vpc"), ws("app", "account-1/eu-west-1/team/app")]);
    const t = new WorkspacesTree(s); (s as any).fire();
    const roots = t.getChildren(); expect(roots[0]).toBeInstanceOf(CloudNode);
    const region = t.getChildren(roots[0])[0]; expect(region).toBeInstanceOf(RegionNode);
    const kids = t.getChildren(region);
    const app = t.getChildren(kids.find((k) => !(k instanceof WorkspaceNode))!)[0] as WorkspaceNode;
    expect(app.ws.name).toBe("app");
    expect(t.getParent(app)?.id).toBe("folder:1/eu-west-1/team");
    expect(t.getParent(t.getParent(app)!)).toBe(region);
    expect(t.getParent(region)).toBe(roots[0]);
    expect(t.getChildren(roots[0])[0]).toBe(region);                 // same instance on re-query
    expect(t.nodeForWorkspace("app")).toBe(app);
  });
});
```

- [ ] **Step 2: Run** → fails (`getParent` returns undefined; `nodeForWorkspace` missing).

- [ ] **Step 3: Implement** in `workspacesTree.ts`: add `private nodes = new Map<string, Node>()` and `private parents = new Map<string, Node | undefined>()`, cleared in the `store.onDidChange` / `session.onDidChange` handlers before firing; a `remember(node, parent)` helper that returns the cached instance for `node.id` if present (else stores it) and records the parent; use it for every node created in `getChildren` (message nodes get no id and are not cached). Implement `getParent(n) { return n.id ? this.parents.get(n.id) : undefined; }`, `nodeForWorkspace(wsId)`: if not cached, walk `this.tree` (cloud → region → folders) building nodes through the same `remember` path until the `ws:<id>` node exists, then return it. `revealWorkspace(view, wsId)`: `const n = this.nodeForWorkspace(wsId); if (n) await view.reveal(n, { select: true, focus: true, expand: true });`. Replace the reveal stub in `extension.ts`.

- [ ] **Step 4: Verify** `npm test` (new test green), typecheck, build. Manual: status-bar quick pick → Reveal in sidebar expands to the workspace.

- [ ] **Step 5: Commit** — `feat(vscode): reveal the current file's workspace in the sidebar` + trailers.

---

### Task 5: Docs, version, smoke tweak

**Files:**
- Modify: `docs/VSCODE.md` (new "Editor integration" section: status bar, actions, branch pinning, `statusBar.enabled`), `services/vscode/package.json` (`"version": "0.2.0"`), spec §5 (add a "Milestone B notes" bullet list: unknown-remote fallback only when unique; branch pin uses `PUT /workspaces/{id}`; git probe cached 10 s / 3 s timeout), `test/integration/suite/smoke.test.ts` (assert `terraducktel.currentFileActions` is a registered command via `vscode.commands.getCommands(true)`).

- [ ] **Step 1: Edit docs, spec, version; extend the smoke assertion.**
- [ ] **Step 2: Verify** `npm run typecheck && npm test && npm run build && npm run test:integration` and `npm run package`.
- [ ] **Step 3: Commit** — `docs(vscode): editor integration; bump to 0.2.0` + trailers.

---

## Self-review checklist (done while writing)

- **Spec §5 coverage:** path resolution (T1+T2), status bar text/click actions (T3), not-imported state with Discover link (T3), branch awareness with pin (T3 via `runCommandFor`), Reveal (T4), docs (T5).
- **Type consistency:** `matchWorkspace` returns `{ws, exact}` used by `EditorStatus.set`; `GitInfo.branch` used for the pin prompt; `runCommandFor(s, ws, command, watch, {branch})` defined in T3 and used by both the commands and the status bar; `revealWorkspace(view, wsId)` produced in T4 and consumed by T3's `deps.reveal` (stubbed until T4).
- **Out of scope:** notifications (milestone C); editing workspace settings; web-based VS Code.
