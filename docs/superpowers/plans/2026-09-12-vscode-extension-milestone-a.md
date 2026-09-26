# VS Code Extension — Milestone A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a VS Code extension (`services/vscode/`) that signs in to a Terraducktel deployment (password / API key / SSO loopback), shows workspaces and runs in a sidebar grouped like the web UI, triggers plan/apply/destroy, tails run steps into an output channel, opens the plan as a diff-coloured document, and approves/rejects runs behind a confirmation modal.

**Architecture:** Native VS Code tree views over a small typed HTTP client (Node `https`, no runtime deps) with a polling store. Credentials live in VS Code SecretStorage; access tokens in memory; 401 → one refresh → one retry. Endpoint usage is pinned by `services/vscode/api_contract.json`, guarded by a backend test cloned from the CLI's. Bundled with esbuild; unit tests with vitest against an in-process fake server; one headless integration smoke via `@vscode/test-electron`.

**Tech Stack:** TypeScript 5 (strict), esbuild, vitest 4, `@types/vscode` ^1.90, `@vscode/test-electron` + mocha for the smoke test, `@vscode/vsce` for packaging. Node 20+ (npm 11 locally).

**Spec:** `docs/superpowers/specs/2026-09-12-vscode-extension-design.md` (milestone A = spec §1–§4, §7–§9)

## Global Constraints

- All extension code lives under `services/vscode/`. No runtime npm dependencies (dev deps only) — the bundle must be self-contained.
- API base: profile `url` is the API origin (e.g. `http://localhost:8001`); every call is `${url}/api/v1${path}`. Every authenticated request carries `Authorization: Bearer <token>` and `X-Business-Unit: <slug>`.
- Secrets (refresh tokens, API keys, access tokens) never appear in settings, logs, output channels, error messages, or test snapshots.
- Auth contract (verbatim from the API): `GET /auth/config` → `{mode, oidc_enabled, oidc_issuer, cli_loopback}`; `POST /auth/token {email,password}` and `POST /auth/refresh {refresh_token}` → `{access_token, refresh_token, token_type}`; SSO: open `${url}/api/v1/auth/oidc/login?cli_port=<port>&cli_nonce=<nonce>` in the browser; the server's page redirects to `http://127.0.0.1:<port>/callback?access_token=…&refresh_token=…&nonce=…`. Access-token JWT payload has `sub, email, role, is_superadmin, type:"access", exp`.
- Data contract: `GET /workspaces` → `WorkspaceResponse[]` with `id, business_unit_id, name, environment, aws_account_id, region, repo_url, tf_working_dir, repo_ref, kind, cluster_id, tags, drift_status, path_status, azure_subscription_id, gcp_project_id, state_backend`. `GET /runs?limit=N&status=a,b&workspace_id=…` → `RunResponse[]` with `id, workspace_id, command, status, branch, created_at, started_at, completed_at, policy_status`. Run statuses: `pending, running, planning, planned, awaiting_approval, applying, applied, failed, cancelled`; terminal = `planned, applied, failed, cancelled` (and `awaiting_approval` ends the plan phase). `GET /runs/{id}/steps?since=<position>&include_output=<bool>` → `[{id, run_id, position, name, status, started_at, completed_at, duration_seconds, output, summary_json}]`. `GET /runs/{id}/graph` → `{nodes, edges, summary:{add, change, destroy, replace?}}`. `GET /runs/{id}/plan` → `{plan_output}`. `POST /workspaces/{id}/runs {command}` → `RunResponse`. `PUT /workspaces/{id} {repo_ref}`. `GET /workspaces/{id}/branches` → `{source, default_branch, branches: string[]}`. `POST /workspaces/{id}/sync`. `POST /runs/{id}/approve|reject|cancel`. `GET /business-units` → `[{id, slug, name}]`.
- Web UI deep links (for "Open in browser"): run → `${uiUrl}/runs/<id>`; runs list → `${uiUrl}/runs`; dashboard → `${uiUrl}/`. `uiUrl` defaults to the profile `url` with a trailing `/api` stripped.
- Extension ids: package name `terraducktel-vscode`, publisher `terraducktel` (placeholder), views container `terraducktel`, views `terraducktel.workspaces` / `terraducktel.runs`, command prefix `terraducktel.`, settings prefix `terraducktel.`, virtual document scheme `tdt-plan`.
- Commit messages: conventional commits; each ends with the two trailer lines `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` then `Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit`.
- Commands: `cd services/vscode && npm run build` (esbuild), `npm test` (vitest), `npm run typecheck` (`tsc --noEmit`), `npm run package` (vsce → `.vsix`), `npm run test:integration` (test-electron; downloads VS Code — may be skipped where the network forbids it, say so in the report). Backend contract test: `cd services/api && ./venv/bin/python -m pytest tests/test_vscode_api_contract.py -q` (`python` is not on PATH; use the venv).

## File structure

| File | Responsibility |
|---|---|
| `services/vscode/package.json` | Manifest: views, commands, menus, settings, activation; scripts; dev deps |
| `services/vscode/tsconfig.json`, `esbuild.mjs`, `vitest.config.ts`, `.vscodeignore`, `media/tdt.svg` | Toolchain |
| `services/vscode/api_contract.json` | Endpoints the extension calls (guarded) |
| `services/api/tests/test_vscode_api_contract.py` | Guard test |
| `src/http.ts` | Tiny `request()` over Node `http`/`https` with optional insecure TLS |
| `src/api/types.ts`, `src/api/client.ts` | Response types; `TdtClient` (headers, refresh-on-401, typed helpers) |
| `src/auth/secrets.ts`, `src/auth/tokenManager.ts`, `src/auth/profiles.ts`, `src/auth/sso.ts`, `src/auth/jwt.ts` | Credential storage, refresh, profiles, loopback SSO, claims |
| `src/state/grouping.ts`, `src/state/store.ts` | Path-convention grouping (port of web `paths.ts`), polling cache |
| `src/views/workspacesTree.ts`, `src/views/runsTree.ts`, `src/views/nodes.ts` | Tree providers |
| `src/output/runOutput.ts`, `src/output/planDocument.ts` | Step tailing, plan virtual document + decorations |
| `src/commands/auth.ts`, `src/commands/workspace.ts`, `src/commands/run.ts` | Command handlers |
| `src/extension.ts` | `activate()` wiring only |
| `test/unit/*.test.ts`, `test/fake-server.ts` | Unit tests + fake TDT server |
| `test/integration/{runTest.ts,suite/index.ts,suite/smoke.test.ts}` | Headless smoke |
| `Makefile`, `.github/workflows/ci-cd.yml`, `docs/VSCODE.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md` | Integration + docs |

---

### Task 1: Scaffold the extension package and toolchain

**Files:**
- Create: `services/vscode/package.json`, `services/vscode/tsconfig.json`, `services/vscode/esbuild.mjs`, `services/vscode/vitest.config.ts`, `services/vscode/.vscodeignore`, `services/vscode/media/tdt.svg`, `services/vscode/src/extension.ts` (stub), `services/vscode/test/unit/smoke.test.ts`, `services/vscode/README.md`
- Modify: `.gitignore` (add `*.vsix`, `.vscode-test/`), `Makefile` (targets)

**Interfaces:**
- Produces: the build/test/typecheck/package scripts every later task runs; the manifest's command/view ids (fixed in Global Constraints) that later tasks implement.

- [ ] **Step 1: Write the failing smoke test**

```ts
// services/vscode/test/unit/smoke.test.ts
import { describe, expect, it } from "vitest";
import { EXTENSION_ID } from "../../src/ids";

describe("scaffold", () => {
  it("exposes the extension id", () => {
    expect(EXTENSION_ID).toBe("terraducktel.terraducktel-vscode");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/vscode && npm test` — expected: fails (no `package.json` yet / cannot resolve `../../src/ids`).

- [ ] **Step 3: Write the manifest and toolchain files**

```json
// services/vscode/package.json
{
  "name": "terraducktel-vscode",
  "displayName": "Terraducktel",
  "description": "Drive Terraducktel (TDT) Terraform runs from VS Code: workspaces, runs, plan output and approvals.",
  "version": "0.1.0",
  "publisher": "terraducktel",
  "license": "MIT",
  "private": true,
  "engines": { "vscode": "^1.90.0", "node": ">=20" },
  "categories": ["Other"],
  "activationEvents": ["onStartupFinished"],
  "main": "./dist/extension.js",
  "contributes": {
    "viewsContainers": {
      "activitybar": [
        { "id": "terraducktel", "title": "Terraducktel", "icon": "media/tdt.svg" }
      ]
    },
    "views": {
      "terraducktel": [
        { "id": "terraducktel.workspaces", "name": "Workspaces" },
        { "id": "terraducktel.runs", "name": "Runs" }
      ]
    },
    "viewsWelcome": [
      {
        "view": "terraducktel.workspaces",
        "contents": "Not signed in to Terraducktel.\n[Sign in](command:terraducktel.signIn)\n\nAdd a profile under Settings → Terraducktel if you have none.",
        "when": "!terraducktel.signedIn"
      }
    ],
    "commands": [
      { "command": "terraducktel.signIn", "title": "Sign in", "category": "Terraducktel" },
      { "command": "terraducktel.signOut", "title": "Sign out", "category": "Terraducktel" },
      { "command": "terraducktel.switchProfile", "title": "Switch profile", "category": "Terraducktel" },
      { "command": "terraducktel.switchBusinessUnit", "title": "Switch business unit", "category": "Terraducktel", "icon": "$(organization)" },
      { "command": "terraducktel.refresh", "title": "Refresh", "category": "Terraducktel", "icon": "$(refresh)" },
      { "command": "terraducktel.plan", "title": "Plan", "category": "Terraducktel", "icon": "$(play)" },
      { "command": "terraducktel.apply", "title": "Apply…", "category": "Terraducktel" },
      { "command": "terraducktel.destroy", "title": "Destroy…", "category": "Terraducktel" },
      { "command": "terraducktel.setBranch", "title": "Set tracked branch…", "category": "Terraducktel" },
      { "command": "terraducktel.syncWorkspace", "title": "Sync from repo", "category": "Terraducktel" },
      { "command": "terraducktel.openInBrowser", "title": "Open in browser", "category": "Terraducktel", "icon": "$(link-external)" },
      { "command": "terraducktel.copyId", "title": "Copy id", "category": "Terraducktel" },
      { "command": "terraducktel.watchRun", "title": "Watch run (show steps)", "category": "Terraducktel", "icon": "$(output)" },
      { "command": "terraducktel.showPlan", "title": "Show plan output", "category": "Terraducktel", "icon": "$(diff)" },
      { "command": "terraducktel.approve", "title": "Approve…", "category": "Terraducktel", "icon": "$(check)" },
      { "command": "terraducktel.reject", "title": "Reject…", "category": "Terraducktel", "icon": "$(close)" },
      { "command": "terraducktel.cancelRun", "title": "Cancel run", "category": "Terraducktel" }
    ],
    "menus": {
      "view/title": [
        { "command": "terraducktel.refresh", "when": "view == terraducktel.workspaces || view == terraducktel.runs", "group": "navigation@1" },
        { "command": "terraducktel.switchBusinessUnit", "when": "view == terraducktel.workspaces && terraducktel.signedIn", "group": "navigation@2" }
      ],
      "view/item/context": [
        { "command": "terraducktel.plan", "when": "view == terraducktel.workspaces && viewItem == workspace && terraducktel.canWrite", "group": "inline@1" },
        { "command": "terraducktel.plan", "when": "view == terraducktel.workspaces && viewItem == workspace && terraducktel.canWrite", "group": "1_run@1" },
        { "command": "terraducktel.apply", "when": "view == terraducktel.workspaces && viewItem == workspace && terraducktel.canWrite", "group": "1_run@2" },
        { "command": "terraducktel.destroy", "when": "view == terraducktel.workspaces && viewItem == workspace && terraducktel.canWrite", "group": "1_run@3" },
        { "command": "terraducktel.setBranch", "when": "view == terraducktel.workspaces && viewItem == workspace && terraducktel.canWrite", "group": "2_edit@1" },
        { "command": "terraducktel.syncWorkspace", "when": "view == terraducktel.workspaces && viewItem == workspace && terraducktel.canWrite", "group": "2_edit@2" },
        { "command": "terraducktel.openInBrowser", "when": "view == terraducktel.workspaces && viewItem == workspace", "group": "3_nav@1" },
        { "command": "terraducktel.copyId", "when": "view == terraducktel.workspaces && viewItem == workspace", "group": "3_nav@2" },
        { "command": "terraducktel.watchRun", "when": "viewItem =~ /^run/", "group": "inline@1" },
        { "command": "terraducktel.showPlan", "when": "viewItem =~ /^run/", "group": "inline@2" },
        { "command": "terraducktel.approve", "when": "viewItem == run.awaiting_approval && terraducktel.canWrite", "group": "inline@3" },
        { "command": "terraducktel.approve", "when": "viewItem == run.awaiting_approval && terraducktel.canWrite", "group": "1_gate@1" },
        { "command": "terraducktel.reject", "when": "viewItem == run.awaiting_approval && terraducktel.canWrite", "group": "1_gate@2" },
        { "command": "terraducktel.cancelRun", "when": "viewItem =~ /^run\\.(pending|running|planning|planned|awaiting_approval)$/ && terraducktel.canWrite", "group": "1_gate@3" },
        { "command": "terraducktel.watchRun", "when": "viewItem =~ /^run/", "group": "2_view@1" },
        { "command": "terraducktel.showPlan", "when": "viewItem =~ /^run/", "group": "2_view@2" },
        { "command": "terraducktel.openInBrowser", "when": "viewItem =~ /^run/", "group": "3_nav@1" },
        { "command": "terraducktel.copyId", "when": "viewItem =~ /^run/", "group": "3_nav@2" }
      ],
      "commandPalette": [
        { "command": "terraducktel.plan", "when": "terraducktel.signedIn" },
        { "command": "terraducktel.apply", "when": "terraducktel.signedIn" },
        { "command": "terraducktel.destroy", "when": "terraducktel.signedIn" },
        { "command": "terraducktel.copyId", "when": "false" },
        { "command": "terraducktel.openInBrowser", "when": "false" }
      ]
    },
    "configuration": {
      "title": "Terraducktel",
      "properties": {
        "terraducktel.profiles": {
          "type": "array",
          "default": [],
          "markdownDescription": "Deployments you can sign in to. Mirrors the `tdt` CLI profile shape.",
          "items": {
            "type": "object",
            "required": ["name", "url"],
            "properties": {
              "name": { "type": "string", "description": "Profile name, e.g. prod." },
              "url": { "type": "string", "description": "API origin, e.g. https://tdt.example.com or http://localhost:8001." },
              "uiUrl": { "type": "string", "description": "Web UI origin for 'Open in browser'. Defaults to url with a trailing /api removed." },
              "bu": { "type": "string", "description": "Default business-unit slug." },
              "insecureTls": { "type": "boolean", "default": false, "description": "Skip TLS certificate verification (self-signed dev stacks only)." }
            }
          }
        },
        "terraducktel.activeProfile": { "type": "string", "default": "", "description": "Name of the profile to use." },
        "terraducktel.refreshIntervalSeconds": { "type": "number", "default": 30, "minimum": 5, "description": "How often the sidebar polls while visible." },
        "terraducktel.runsLimit": { "type": "number", "default": 200, "minimum": 1, "maximum": 1000, "description": "How many recent runs to fetch." },
        "terraducktel.trace": { "type": "boolean", "default": false, "description": "Log request/response metadata (never credentials) to the Terraducktel output channel." }
      }
    }
  },
  "scripts": {
    "build": "node esbuild.mjs",
    "watch": "node esbuild.mjs --watch",
    "typecheck": "tsc --noEmit -p .",
    "test": "vitest run",
    "test:integration": "npm run build && node ./out-test/integration/runTest.js",
    "pretest:integration": "tsc -p tsconfig.test.json",
    "package": "npm run build && vsce package --no-dependencies --out terraducktel-vscode.vsix"
  },
  "devDependencies": {
    "@types/mocha": "^10.0.9",
    "@types/node": "^20.17.0",
    "@types/vscode": "^1.90.0",
    "@vscode/test-electron": "^2.4.1",
    "@vscode/vsce": "^3.2.1",
    "esbuild": "^0.24.2",
    "mocha": "^10.8.2",
    "typescript": "^5.6.3",
    "vitest": "^4.1.10"
  }
}
```

```json
// services/vscode/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "moduleResolution": "node",
    "lib": ["ES2022"],
    "strict": true,
    "noUnusedLocals": true,
    "noImplicitReturns": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "sourceMap": true,
    "rootDir": ".",
    "outDir": "out",
    "types": ["node", "vscode"]
  },
  "include": ["src/**/*.ts", "test/**/*.ts"],
  "exclude": ["node_modules", "dist", "out", "out-test", ".vscode-test"]
}
```

```json
// services/vscode/tsconfig.test.json  (integration harness only — compiled to out-test/)
{
  "extends": "./tsconfig.json",
  "compilerOptions": { "outDir": "out-test", "rootDir": "test", "types": ["node", "vscode", "mocha"] },
  "include": ["test/integration/**/*.ts"]
}
```

```js
// services/vscode/esbuild.mjs
import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");
/** @type {import("esbuild").BuildOptions} */
const options = {
  entryPoints: ["src/extension.ts"],
  bundle: true,
  outfile: "dist/extension.js",
  // `vscode` is provided by the host; everything else must be bundled (no runtime deps).
  external: ["vscode"],
  platform: "node",
  target: "node20",
  format: "cjs",
  sourcemap: true,
  minify: false,
  logLevel: "info",
};
if (watch) {
  const ctx = await esbuild.context(options);
  await ctx.watch();
} else {
  await esbuild.build(options);
}
```

```ts
// services/vscode/vitest.config.ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/unit/**/*.test.ts"],
    environment: "node",
    // The real `vscode` module only exists inside the editor; unit tests
    // exercise pure modules and get a stub for anything that imports it.
    alias: { vscode: new URL("./test/unit/vscode-stub.ts", import.meta.url).pathname },
  },
});
```

```ts
// services/vscode/test/unit/vscode-stub.ts
// Minimal stand-in so pure modules that `import * as vscode` for types or
// ThemeColor/EventEmitter can load under vitest. Extend as tests need.
export class EventEmitter<T> {
  private listeners: Array<(e: T) => void> = [];
  event = (l: (e: T) => void) => { this.listeners.push(l); return { dispose: () => { this.listeners = this.listeners.filter((x) => x !== l); } }; };
  fire(e: T) { for (const l of [...this.listeners]) l(e); }
  dispose() { this.listeners = []; }
}
export class ThemeColor { constructor(public id: string) {} }
export class ThemeIcon { constructor(public id: string, public color?: ThemeColor) {} }
export enum TreeItemCollapsibleState { None = 0, Collapsed = 1, Expanded = 2 }
export class TreeItem {
  label?: string; description?: string; tooltip?: unknown; contextValue?: string; iconPath?: unknown; command?: unknown; id?: string;
  constructor(label: string, public collapsibleState: TreeItemCollapsibleState = TreeItemCollapsibleState.None) { this.label = label; }
}
export class MarkdownString { value = ""; constructor(v = "") { this.value = v; } appendMarkdown(s: string) { this.value += s; return this; } }
export const Uri = { parse: (s: string) => ({ toString: () => s, scheme: s.split(":")[0], path: s.split(":").slice(1).join(":") }) };
export const window = { createOutputChannel: () => ({ appendLine() {}, append() {}, show() {}, clear() {}, dispose() {} }) };
export const commands = { executeCommand: async () => undefined };
export const env = { openExternal: async () => true, clipboard: { writeText: async () => undefined } };
```

```
# services/vscode/.vscodeignore
.vscode-test/**
out/**
out-test/**
node_modules/**
src/**
test/**
esbuild.mjs
vitest.config.ts
tsconfig*.json
*.vsix
```

```svg
<!-- services/vscode/media/tdt.svg — monochrome activity-bar mark (a duck-ish cloud + check) -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M7 17a4 4 0 0 1 1-7.9 6 6 0 0 1 11 2A3.5 3.5 0 0 1 18 18H7z"/>
  <path d="M9.5 13.5l2 2 3.5-4"/>
</svg>
```

```ts
// services/vscode/src/ids.ts
export const PUBLISHER = "terraducktel";
export const EXTENSION_NAME = "terraducktel-vscode";
export const EXTENSION_ID = `${PUBLISHER}.${EXTENSION_NAME}`;
export const VIEW_WORKSPACES = "terraducktel.workspaces";
export const VIEW_RUNS = "terraducktel.runs";
export const PLAN_SCHEME = "tdt-plan";
export const CTX_SIGNED_IN = "terraducktel.signedIn";
export const CTX_CAN_WRITE = "terraducktel.canWrite";
```

```ts
// services/vscode/src/extension.ts  (stub — replaced in Task 7)
import * as vscode from "vscode";

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("terraducktel.signIn", () => {
      void vscode.window.showInformationMessage("Terraducktel: not wired yet");
    }),
  );
}

export function deactivate(): void {}
```

```md
<!-- services/vscode/README.md -->
# Terraducktel for VS Code

Workspaces, runs, plan output and approvals for a Terraducktel deployment, inside VS Code.
See `docs/VSCODE.md` in the repo for setup. Build: `npm run build`; package: `npm run package`.
```

Append to `.gitignore`:

```
*.vsix
.vscode-test/
out-test/
```

Add to `Makefile` after `test-ui:`:

```make
test-vscode:
	cd services/vscode && npm run typecheck && npm test

build-vscode:
	cd services/vscode && npm run package
```

and add `test-vscode` to the aggregate `test:` line.

- [ ] **Step 4: Install and verify**

Run:
```bash
cd services/vscode && npm install && npm run typecheck && npm run build && npm test
```
Expected: `dist/extension.js` produced; vitest `1 passed`. Then `npx vsce package --no-dependencies --out /tmp/scaffold.vsix` must succeed (fix any manifest complaint vsce prints; commit `package-lock.json`).

- [ ] **Step 5: Commit**

```bash
git add services/vscode .gitignore Makefile
git commit -m "feat(vscode): scaffold the Terraducktel VS Code extension

Manifest (views, commands, menus, settings), esbuild bundle, vitest unit
harness with a vscode stub, vsce packaging, and make targets.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 2: API contract file + backend guard test

**Files:**
- Create: `services/vscode/api_contract.json`, `services/api/tests/test_vscode_api_contract.py`

- [ ] **Step 1: Write the guard test (it fails until the contract exists)**

```python
# services/api/tests/test_vscode_api_contract.py
"""Guard: every endpoint the VS Code extension calls must still exist in this
API. Same idea as test_cli_api_contract.py — `services/vscode/api_contract.json`
names every path the extension depends on; if a router moves or renames one,
this test names the extension feature that just broke."""
import json
from pathlib import Path

import pytest

CONTRACT = Path(__file__).resolve().parents[2] / "vscode" / "api_contract.json"
PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def openapi() -> dict:
    from app.main import app

    return app.openapi()


@pytest.fixture(scope="module")
def contract() -> list[dict]:
    assert CONTRACT.exists(), f"VS Code contract missing at {CONTRACT}"
    return json.loads(CONTRACT.read_text())["endpoints"]


def test_contract_file_is_non_trivial(contract):
    assert len(contract) >= 15, "the contract looks truncated"


def test_every_extension_endpoint_exists_in_the_api(openapi, contract):
    paths = openapi["paths"]
    missing = []
    for entry in contract:
        full = PREFIX + entry["path"]
        methods = {m.lower() for m in (paths.get(full) or {})}
        if entry["method"].lower() not in methods:
            missing.append(f"{entry['method']} {full} (used by: {entry.get('used_by', '?')})")
    assert not missing, "extension depends on endpoints the API no longer serves:\n  " + "\n  ".join(missing)


def test_contract_entries_are_well_formed(contract):
    for e in contract:
        assert e["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE"}, e
        assert e["path"].startswith("/") and not e["path"].startswith(PREFIX), e
        assert e.get("used_by"), f"{e['method']} {e['path']} has no used_by"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/api && ./venv/bin/python -m pytest tests/test_vscode_api_contract.py -q` — expected: fails on "VS Code contract missing".

- [ ] **Step 3: Write the contract**

```json
{
  "_comment": "Every API endpoint the VS Code extension calls. Guarded by services/api/tests/test_vscode_api_contract.py. Paths are relative to /api/v1.",
  "endpoints": [
    { "method": "GET", "path": "/auth/config", "used_by": "Sign in (provider discovery: sso vs password)" },
    { "method": "POST", "path": "/auth/token", "used_by": "Sign in with password" },
    { "method": "GET", "path": "/auth/oidc/login", "used_by": "Sign in with SSO (browser is sent here; the extension never fetches it)", "browser": true },
    { "method": "POST", "path": "/auth/refresh", "used_by": "automatic token refresh" },
    { "method": "GET", "path": "/business-units", "used_by": "Switch business unit" },
    { "method": "GET", "path": "/workspaces", "used_by": "Workspaces view" },
    { "method": "GET", "path": "/workspaces/{workspace_id}", "used_by": "workspace tooltip refresh" },
    { "method": "PUT", "path": "/workspaces/{workspace_id}", "used_by": "Set tracked branch" },
    { "method": "GET", "path": "/workspaces/{workspace_id}/branches", "used_by": "Set tracked branch (picker)" },
    { "method": "POST", "path": "/workspaces/{workspace_id}/sync", "used_by": "Sync from repo" },
    { "method": "POST", "path": "/workspaces/{workspace_id}/runs", "used_by": "Plan / Apply / Destroy" },
    { "method": "GET", "path": "/runs", "used_by": "Runs view, workspace run children" },
    { "method": "GET", "path": "/runs/{run_id}", "used_by": "Watch run (status poll)" },
    { "method": "GET", "path": "/runs/{run_id}/steps", "used_by": "Watch run (step tailing with ?since=)" },
    { "method": "GET", "path": "/runs/{run_id}/graph", "used_by": "Approve… confirmation summary" },
    { "method": "GET", "path": "/runs/{run_id}/plan", "used_by": "Show plan output" },
    { "method": "POST", "path": "/runs/{run_id}/approve", "used_by": "Approve…" },
    { "method": "POST", "path": "/runs/{run_id}/reject", "used_by": "Reject…" },
    { "method": "POST", "path": "/runs/{run_id}/cancel", "used_by": "Cancel run" }
  ]
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd services/api && ./venv/bin/python -m pytest tests/test_vscode_api_contract.py -q` — expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/vscode/api_contract.json services/api/tests/test_vscode_api_contract.py
git commit -m "test(api): guard the VS Code extension's API contract

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 3: HTTP layer and typed API client

**Files:**
- Create: `services/vscode/src/http.ts`, `services/vscode/src/api/types.ts`, `services/vscode/src/api/client.ts`, `services/vscode/test/fake-server.ts`, `services/vscode/test/unit/client.test.ts`

**Interfaces:**
- Produces: `request(opts) → {status, headers, body}` (Node http/https, JSON body, `insecureTls`); `TdtClient` with `getJson/postJson/putJson/deleteJson` plus typed helpers `listWorkspaces()`, `getWorkspace(id)`, `updateWorkspace(id, patch)`, `listBranches(id)`, `syncWorkspace(id)`, `triggerRun(id, body)`, `listRuns(q)`, `getRun(id)`, `getSteps(id, since?)`, `getGraph(id)`, `getPlan(id)`, `approve(id)`, `reject(id, reason?)`, `cancel(id)`, `listBusinessUnits()`, `authConfig()`, `login(email,pw)`, `refresh(token)`; `ApiError {status, detail}`; `TdtClient` takes a `TokenProvider` (Task 4) and emits `onSignedOut`.

- [ ] **Step 1: Write the fake server and failing client tests**

```ts
// services/vscode/test/fake-server.ts
import * as http from "node:http";
import { AddressInfo } from "node:net";

export type Handler = (req: http.IncomingMessage, body: string, res: http.ServerResponse) => void;

/** In-process TDT stand-in. Register handlers by "METHOD /api/v1/path" (exact) or a RegExp. */
export class FakeServer {
  private server: http.Server;
  public calls: Array<{ method: string; url: string; headers: http.IncomingHttpHeaders; body: string }> = [];
  private routes: Array<{ m: string; p: string | RegExp; h: Handler }> = [];
  constructor() {
    this.server = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        this.calls.push({ method: req.method!, url: req.url!, headers: req.headers, body });
        const path = req.url!.split("?")[0];
        const r = this.routes.find((x) => x.m === req.method && (typeof x.p === "string" ? x.p === path : x.p.test(path)));
        if (!r) { res.writeHead(404, { "content-type": "application/json" }); res.end(JSON.stringify({ detail: "Not Found" })); return; }
        r.h(req, body, res);
      });
    });
  }
  on(method: string, path: string | RegExp, h: Handler) { this.routes.push({ m: method, p: path, h }); return this; }
  json(method: string, path: string | RegExp, status: number, payload: unknown) {
    return this.on(method, path, (_q, _b, res) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(payload)); });
  }
  async start(): Promise<string> {
    await new Promise<void>((ok) => this.server.listen(0, "127.0.0.1", ok));
    const { port } = this.server.address() as AddressInfo;
    return `http://127.0.0.1:${port}`;
  }
  async stop() { await new Promise<void>((ok) => this.server.close(() => ok())); }
  requests(method: string, pathPrefix: string) { return this.calls.filter((c) => c.method === method && c.url.startsWith(pathPrefix)); }
}
```

```ts
// services/vscode/test/unit/client.test.ts
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { ApiError, TdtClient } from "../../src/api/client";
import type { TokenProvider } from "../../src/api/client";

function tokens(initial = "acc1"): TokenProvider & { access: string; refreshed: number; signedOut: number } {
  const t = {
    access: initial, refreshed: 0, signedOut: 0,
    getAccessToken: async () => t.access,
    refreshAccessToken: async () => { t.refreshed++; t.access = `acc${t.refreshed + 1}`; return t.access; },
    signOut: async () => { t.signedOut++; },
  };
  return t;
}

describe("TdtClient", () => {
  let srv: FakeServer; let url: string;
  beforeEach(async () => { srv = new FakeServer(); url = await srv.start(); });
  afterEach(async () => { await srv.stop(); });

  it("sends bearer + BU headers and parses JSON", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, [{ id: "w1", name: "vpc" }]);
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    const ws = await c.listWorkspaces();
    expect(ws[0].id).toBe("w1");
    const call = srv.calls[0];
    expect(call.headers.authorization).toBe("Bearer acc1");
    expect(call.headers["x-business-unit"]).toBe("default");
    expect(call.headers.accept).toContain("application/json");
  });

  it("encodes query params", async () => {
    srv.json("GET", "/api/v1/runs", 200, []);
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    await c.listRuns({ limit: 50, status: ["failed", "cancelled"], workspace_id: "w 1" });
    expect(srv.calls[0].url).toBe("/api/v1/runs?limit=50&status=failed%2Ccancelled&workspace_id=w%201");
  });

  it("on 401 refreshes once and retries with the new token", async () => {
    let n = 0;
    srv.on("GET", "/api/v1/workspaces", (req, _b, res) => {
      n++;
      if (req.headers.authorization === "Bearer acc1") { res.writeHead(401, { "content-type": "application/json" }); res.end(JSON.stringify({ detail: "expired" })); return; }
      res.writeHead(200, { "content-type": "application/json" }); res.end("[]");
    });
    const t = tokens(); const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    expect(await c.listWorkspaces()).toEqual([]);
    expect(n).toBe(2); expect(t.refreshed).toBe(1); expect(srv.calls[1].headers.authorization).toBe("Bearer acc2");
  });

  it("a second 401 signs out and throws ApiError(401)", async () => {
    srv.json("GET", "/api/v1/workspaces", 401, { detail: "nope" });
    const t = tokens(); const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    let out = 0; c.onSignedOut(() => out++);
    await expect(c.listWorkspaces()).rejects.toMatchObject({ status: 401 });
    expect(t.refreshed).toBe(1); expect(t.signedOut).toBe(1); expect(out).toBe(1);
    expect(srv.requests("GET", "/api/v1/workspaces").length).toBe(2);
  });

  it("serialises concurrent refreshes (one refresh for N parallel 401s)", async () => {
    srv.on("GET", "/api/v1/workspaces", (req, _b, res) => {
      if (req.headers.authorization === "Bearer acc1") { res.writeHead(401, { "content-type": "application/json" }); res.end("{}"); return; }
      res.writeHead(200, { "content-type": "application/json" }); res.end("[]");
    });
    const t = tokens(); const c = new TdtClient({ baseUrl: url, bu: "default", tokens: t });
    await Promise.all([c.listWorkspaces(), c.listWorkspaces(), c.listWorkspaces()]);
    expect(t.refreshed).toBe(1);
  });

  it("surfaces the API detail string on errors", async () => {
    srv.json("POST", "/api/v1/workspaces/w1/runs", 409, { detail: "workspace is locked" });
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    const err = await c.triggerRun("w1", { command: "plan" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError); expect(err.status).toBe(409); expect(err.message).toBe("workspace is locked");
  });

  it("flattens pydantic 422 detail arrays", async () => {
    srv.json("POST", "/api/v1/auth/token", 422, { detail: [{ loc: ["body", "email"], msg: "field required" }] });
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    await expect(c.login("", "x")).rejects.toMatchObject({ message: "email: field required" });
  });

  it("does not send auth headers on public auth endpoints", async () => {
    srv.json("GET", "/api/v1/auth/config", 200, { mode: "local", oidc_enabled: false, cli_loopback: true });
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    await c.authConfig();
    expect(srv.calls[0].headers.authorization).toBeUndefined();
  });

  it("builds steps URLs with since/include_output", async () => {
    srv.json("GET", "/api/v1/runs/r1/steps", 200, []);
    const c = new TdtClient({ baseUrl: url, bu: "default", tokens: tokens() });
    await c.getSteps("r1", 3);
    expect(srv.calls[0].url).toBe("/api/v1/runs/r1/steps?since=3");
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd services/vscode && npm test` → cannot resolve `../../src/api/client`.

- [ ] **Step 3: Implement**

```ts
// services/vscode/src/http.ts
import * as http from "node:http";
import * as https from "node:https";
import { URL } from "node:url";

export interface HttpRequest {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  url: string;
  headers?: Record<string, string>;
  body?: unknown;            // JSON-encoded when defined
  timeoutMs?: number;        // default 30 000
  insecureTls?: boolean;     // https only: skip certificate verification
}
export interface HttpResponse { status: number; headers: http.IncomingHttpHeaders; text: string }

/** Minimal JSON-over-HTTP using Node's built-ins, so the bundle has no runtime deps
 *  and per-profile insecure TLS never touches process-global settings. */
export function request(opts: HttpRequest): Promise<HttpResponse> {
  const u = new URL(opts.url);
  const isHttps = u.protocol === "https:";
  const payload = opts.body === undefined ? undefined : Buffer.from(JSON.stringify(opts.body), "utf8");
  const headers: Record<string, string> = { accept: "application/json", ...(opts.headers ?? {}) };
  if (payload) { headers["content-type"] = "application/json"; headers["content-length"] = String(payload.length); }
  const reqOpts: https.RequestOptions = {
    method: opts.method, hostname: u.hostname, port: u.port || (isHttps ? 443 : 80),
    path: u.pathname + u.search, headers, timeout: opts.timeoutMs ?? 30_000,
    ...(isHttps && opts.insecureTls ? { rejectUnauthorized: false } : {}),
  };
  return new Promise((resolve, reject) => {
    const req = (isHttps ? https : http).request(reqOpts, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (c: Buffer) => chunks.push(c));
      res.on("end", () => resolve({ status: res.statusCode ?? 0, headers: res.headers, text: Buffer.concat(chunks).toString("utf8") }));
    });
    req.on("timeout", () => req.destroy(new Error(`request to ${u.host} timed out`)));
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}
```

```ts
// services/vscode/src/api/types.ts
export interface AuthConfig { mode: string; oidc_enabled: boolean; oidc_issuer?: string | null; cli_loopback?: boolean }
export interface TokenPair { access_token: string; refresh_token: string; token_type?: string }
export interface BusinessUnit { id: string; slug: string; name: string }
export interface Workspace {
  id: string; business_unit_id: string; name: string; environment: string;
  aws_account_id: string; region: string; repo_url?: string | null; tf_working_dir: string;
  repo_ref: string; kind: string; cluster_id?: string | null; tags: Record<string, string>;
  drift_status: string; path_status: string; azure_subscription_id?: string | null;
  gcp_project_id?: string | null; state_backend: string; created_at?: string | null;
}
export type RunStatus = "pending" | "running" | "planning" | "planned" | "awaiting_approval" | "applying" | "applied" | "failed" | "cancelled";
export const TERMINAL_RUN_STATUSES: ReadonlySet<string> = new Set(["planned", "applied", "failed", "cancelled"]);
/** Statuses at which a plan-phase watch may stop: the plan has landed and a human is needed. */
export const PLAN_LANDED_STATUSES: ReadonlySet<string> = new Set([...TERMINAL_RUN_STATUSES, "awaiting_approval"]);
export interface Run {
  id: string; workspace_id: string; command: string; status: RunStatus | string; branch?: string | null;
  triggered_by?: string | null; policy_status?: string; created_at?: string | null;
  started_at?: string | null; completed_at?: string | null;
}
export interface RunStep {
  id: string; run_id: string; position: number; name: string; status: string;
  started_at?: string | null; completed_at?: string | null; duration_seconds?: number | null;
  output?: string | null; summary_json?: string | null;
}
export interface GraphSummary { add?: number; change?: number; destroy?: number; replace?: number }
export interface RunGraph { nodes: unknown[]; edges: unknown[]; summary: GraphSummary }
export interface Branches { source: string; default_branch?: string | null; branches: string[] }
export interface TriggerRunBody { command: "plan" | "apply" | "destroy"; branch?: string }
```

```ts
// services/vscode/src/api/client.ts
import { request } from "../http";
import type * as T from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) { super(message); this.name = "ApiError"; }
}

/** Supplied by the token manager (Task 4). The client never stores tokens itself. */
export interface TokenProvider {
  getAccessToken(): Promise<string | undefined>;
  /** Obtain a fresh access token (refresh flow, or re-read an API key). Returns undefined when impossible. */
  refreshAccessToken(): Promise<string | undefined>;
  signOut(): Promise<void>;
}

export interface ClientOptions {
  baseUrl: string; bu: string; tokens: TokenProvider; insecureTls?: boolean;
  trace?: (line: string) => void;
}

function detailToMessage(status: number, text: string): { message: string; detail: unknown } {
  try {
    const j = JSON.parse(text);
    const d = j?.detail;
    if (typeof d === "string") return { message: d, detail: d };
    if (Array.isArray(d)) {
      const msg = d.map((e) => `${(e.loc ?? []).filter((x: unknown) => x !== "body").join(".")}: ${e.msg}`).join("; ");
      return { message: msg || `HTTP ${status}`, detail: d };
    }
    return { message: `HTTP ${status}`, detail: j };
  } catch { return { message: text?.trim() || `HTTP ${status}`, detail: text }; }
}

export class TdtClient {
  private refreshing: Promise<string | undefined> | null = null;
  private signedOutListeners: Array<() => void> = [];
  constructor(private readonly o: ClientOptions) {}

  get baseUrl() { return this.o.baseUrl; }
  get bu() { return this.o.bu; }
  withBu(bu: string) { return new TdtClient({ ...this.o, bu }); }
  onSignedOut(l: () => void) { this.signedOutListeners.push(l); return { dispose: () => { this.signedOutListeners = this.signedOutListeners.filter((x) => x !== l); } }; }

  // ─── core ────────────────────────────────────────────────────────────────
  private url(path: string, query?: Record<string, string | number | string[] | undefined>) {
    const qs = Object.entries(query ?? {})
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(Array.isArray(v) ? v.join(",") : String(v))}`)
      .join("&");
    return `${this.o.baseUrl.replace(/\/+$/, "")}/api/v1${path}${qs ? `?${qs}` : ""}`;
  }

  private async send<R>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, opts: { query?: Record<string, string | number | string[] | undefined>; body?: unknown; auth?: boolean } = {}): Promise<R> {
    const auth = opts.auth !== false;
    const attempt = async (token: string | undefined) => {
      const headers: Record<string, string> = {};
      if (auth) { if (token) headers.authorization = `Bearer ${token}`; if (this.o.bu) headers["x-business-unit"] = this.o.bu; }
      const started = Date.now();
      const res = await request({ method, url: this.url(path, opts.query), headers, body: opts.body, insecureTls: this.o.insecureTls });
      this.o.trace?.(`${method} ${path} → ${res.status} (${Date.now() - started} ms)`);
      return res;
    };
    let res = await attempt(auth ? await this.o.tokens.getAccessToken() : undefined);
    if (auth && res.status === 401) {
      const fresh = await this.refreshOnce();
      if (fresh) res = await attempt(fresh);
      if (res.status === 401) { await this.o.tokens.signOut(); for (const l of [...this.signedOutListeners]) l(); }
    }
    if (res.status >= 400) { const { message, detail } = detailToMessage(res.status, res.text); throw new ApiError(res.status, message, detail); }
    if (res.status === 204 || !res.text) return undefined as R;
    return JSON.parse(res.text) as R;
  }

  /** Coalesce parallel 401s into a single refresh so the refresh token is used once. */
  private refreshOnce(): Promise<string | undefined> {
    if (!this.refreshing) {
      this.refreshing = this.o.tokens.refreshAccessToken().finally(() => { this.refreshing = null; });
    }
    return this.refreshing;
  }

  getJson<R>(path: string, query?: Record<string, string | number | string[] | undefined>) { return this.send<R>("GET", path, { query }); }
  postJson<R>(path: string, body?: unknown) { return this.send<R>("POST", path, { body }); }
  putJson<R>(path: string, body: unknown) { return this.send<R>("PUT", path, { body }); }
  deleteJson<R>(path: string) { return this.send<R>("DELETE", path); }

  // ─── auth (public) ───────────────────────────────────────────────────────
  authConfig() { return this.send<T.AuthConfig>("GET", "/auth/config", { auth: false }); }
  login(email: string, password: string) { return this.send<T.TokenPair>("POST", "/auth/token", { body: { email, password }, auth: false }); }
  refresh(refresh_token: string) { return this.send<T.TokenPair>("POST", "/auth/refresh", { body: { refresh_token }, auth: false }); }
  ssoLoginUrl(port: number, nonce: string) { return this.url("/auth/oidc/login", { cli_port: port, cli_nonce: nonce }); }

  // ─── data ────────────────────────────────────────────────────────────────
  listBusinessUnits() { return this.getJson<T.BusinessUnit[]>("/business-units"); }
  listWorkspaces() { return this.getJson<T.Workspace[]>("/workspaces"); }
  getWorkspace(id: string) { return this.getJson<T.Workspace>(`/workspaces/${enc(id)}`); }
  updateWorkspace(id: string, patch: Partial<Pick<T.Workspace, "repo_ref">>) { return this.putJson<T.Workspace>(`/workspaces/${enc(id)}`, patch); }
  listBranches(id: string) { return this.getJson<T.Branches>(`/workspaces/${enc(id)}/branches`); }
  syncWorkspace(id: string) { return this.postJson<unknown>(`/workspaces/${enc(id)}/sync`); }
  triggerRun(id: string, body: T.TriggerRunBody) { return this.postJson<T.Run>(`/workspaces/${enc(id)}/runs`, body); }
  listRuns(q: { limit?: number; status?: string[]; workspace_id?: string } = {}) { return this.getJson<T.Run[]>("/runs", { limit: q.limit, status: q.status, workspace_id: q.workspace_id }); }
  getRun(id: string) { return this.getJson<T.Run>(`/runs/${enc(id)}`); }
  getSteps(id: string, since?: number, includeOutput = true) { return this.getJson<T.RunStep[]>(`/runs/${enc(id)}/steps`, { since, include_output: includeOutput ? undefined : "false" }); }
  getGraph(id: string) { return this.getJson<T.RunGraph>(`/runs/${enc(id)}/graph`); }
  getPlan(id: string) { return this.getJson<{ plan_output: string | null }>(`/runs/${enc(id)}/plan`); }
  approve(id: string) { return this.postJson<unknown>(`/runs/${enc(id)}/approve`); }
  reject(id: string, reason?: string) { return this.postJson<unknown>(`/runs/${enc(id)}/reject`, reason ? { reason } : undefined); }
  cancel(id: string) { return this.postJson<unknown>(`/runs/${enc(id)}/cancel`); }
}

const enc = encodeURIComponent;
```

Note for the implementer: if `POST /runs/{id}/reject` rejects an unexpected body (422), send no body at all — check `services/api/app/routers/runs.py` for the reject signature and match it; record what you found in the report.

- [ ] **Step 4: Run tests** — `npm test` → all client tests pass; `npm run typecheck` clean.

- [ ] **Step 5: Commit**

```bash
git add services/vscode/src/http.ts services/vscode/src/api services/vscode/test
git commit -m "feat(vscode): typed TDT API client with refresh-on-401

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 4: Credentials — secrets, token manager, profiles, SSO loopback

**Files:**
- Create: `services/vscode/src/auth/secrets.ts`, `src/auth/jwt.ts`, `src/auth/tokenManager.ts`, `src/auth/profiles.ts`, `src/auth/sso.ts`, `test/unit/tokenManager.test.ts`, `test/unit/sso.test.ts`, `test/unit/jwt.test.ts`

**Interfaces:**
- Consumes: `TdtClient.login/refresh/ssoLoginUrl`, `TokenProvider` (Task 3).
- Produces: `SecretStore` interface + `MemorySecretStore`; `decodeJwtPayload(token)`; `TokenManager` implementing `TokenProvider` with `signInWithPassword(client, email, pw)`, `signInWithApiKey(key)`, `signInWithTokenPair(pair)`, `signOut()`, `isSignedIn()`, `claims()` → `{email?, role?, is_superadmin?} | undefined`, `kind()`; `Profile {name,url,uiUrl?,bu?,insecureTls?}`, `readProfiles(cfg)`, `uiUrlFor(profile)`; `runLoopbackLogin({openUrl, buildUrl, timeoutMs})` → `TokenPair`.

- [ ] **Step 1: Failing tests**

```ts
// services/vscode/test/unit/jwt.test.ts
import { describe, expect, it } from "vitest";
import { decodeJwtPayload } from "../../src/auth/jwt";
const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
describe("decodeJwtPayload", () => {
  it("decodes the payload segment", () => {
    const tok = `${b64({ alg: "HS256" })}.${b64({ sub: "u1", email: "a@b", role: "operator", is_superadmin: false, type: "access", exp: 1 })}.sig`;
    expect(decodeJwtPayload(tok)).toMatchObject({ email: "a@b", role: "operator", is_superadmin: false });
  });
  it("returns undefined for garbage / API keys", () => {
    expect(decodeJwtPayload("tdt_abc")).toBeUndefined();
    expect(decodeJwtPayload("a.b")).toBeUndefined();
  });
});
```

```ts
// services/vscode/test/unit/tokenManager.test.ts
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import { MemorySecretStore } from "../../src/auth/secrets";
import { TokenManager } from "../../src/auth/tokenManager";

const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
const jwt = (p: object) => `${b64({ alg: "none" })}.${b64(p)}.x`;

describe("TokenManager", () => {
  let srv: FakeServer; let url: string; let store: MemorySecretStore; let tm: TokenManager; let client: TdtClient;
  beforeEach(async () => {
    srv = new FakeServer(); url = await srv.start(); store = new MemorySecretStore();
    tm = new TokenManager(store, "prod");
    client = new TdtClient({ baseUrl: url, bu: "default", tokens: tm });
    tm.attach(client);
  });
  afterEach(async () => { await srv.stop(); });

  it("password sign-in stores only the refresh token, keeps access in memory", async () => {
    srv.json("POST", "/api/v1/auth/token", 200, { access_token: jwt({ email: "a@b", role: "admin", type: "access" }), refresh_token: jwt({ type: "refresh" }) });
    await tm.signInWithPassword("a@b", "pw");
    expect(await tm.getAccessToken()).toContain(".");
    const raw = JSON.parse((await store.get("terraducktel.cred.prod"))!);
    expect(raw.kind).toBe("password"); expect(raw.refresh_token).toBeDefined(); expect(raw.access_token).toBeUndefined();
    expect(tm.claims()).toMatchObject({ email: "a@b", role: "admin" }); expect(tm.isSignedIn()).toBe(true);
  });

  it("refreshAccessToken redeems the refresh token and rotates it", async () => {
    await store.store("terraducktel.cred.prod", JSON.stringify({ kind: "password", refresh_token: "r1" }));
    srv.json("POST", "/api/v1/auth/refresh", 200, { access_token: jwt({ role: "viewer" }), refresh_token: "r2" });
    const acc = await tm.refreshAccessToken();
    expect(acc).toContain("."); expect(JSON.parse(srv.calls[0].body)).toEqual({ refresh_token: "r1" });
    expect(JSON.parse((await store.get("terraducktel.cred.prod"))!).refresh_token).toBe("r2");
  });

  it("API key is returned as the access token and never refreshed", async () => {
    await tm.signInWithApiKey("tdt_secret");
    expect(await tm.getAccessToken()).toBe("tdt_secret");
    expect(await tm.refreshAccessToken()).toBe("tdt_secret");
    expect(tm.kind()).toBe("api_key"); expect(tm.claims()).toBeUndefined();
  });

  it("refresh failure returns undefined and signOut wipes the secret", async () => {
    await store.store("terraducktel.cred.prod", JSON.stringify({ kind: "password", refresh_token: "dead" }));
    srv.json("POST", "/api/v1/auth/refresh", 401, { detail: "invalid" });
    expect(await tm.refreshAccessToken()).toBeUndefined();
    await tm.signOut();
    expect(await store.get("terraducktel.cred.prod")).toBeUndefined(); expect(tm.isSignedIn()).toBe(false);
  });

  it("restores a stored credential on construction", async () => {
    await store.store("terraducktel.cred.prod", JSON.stringify({ kind: "api_key", api_key: "tdt_k" }));
    const tm2 = new TokenManager(store, "prod"); await tm2.restore();
    expect(tm2.isSignedIn()).toBe(true); expect(await tm2.getAccessToken()).toBe("tdt_k");
  });
});
```

```ts
// services/vscode/test/unit/sso.test.ts
import { describe, expect, it } from "vitest";
import * as http from "node:http";
import { runLoopbackLogin } from "../../src/auth/sso";

function hit(url: string) { return new Promise<number>((ok) => http.get(url, (r) => { r.resume(); ok(r.statusCode ?? 0); })); }

describe("runLoopbackLogin", () => {
  it("resolves with the token pair when the callback carries the right nonce", async () => {
    let captured!: { port: number; nonce: string };
    const p = runLoopbackLogin({ buildUrl: (port, nonce) => { captured = { port, nonce }; return `http://x/login?cli_port=${port}&cli_nonce=${nonce}`; }, openUrl: async () => true, timeoutMs: 5000 });
    await new Promise((r) => setTimeout(r, 20));
    const status = await hit(`http://127.0.0.1:${captured.port}/callback?access_token=A&refresh_token=R&nonce=${captured.nonce}`);
    expect(status).toBe(200);
    await expect(p).resolves.toEqual({ access_token: "A", refresh_token: "R" });
  });

  it("rejects a callback with a wrong nonce and keeps waiting, then times out", async () => {
    let captured!: { port: number; nonce: string };
    const p = runLoopbackLogin({ buildUrl: (port, nonce) => { captured = { port, nonce }; return "http://x"; }, openUrl: async () => true, timeoutMs: 300 });
    await new Promise((r) => setTimeout(r, 20));
    expect(await hit(`http://127.0.0.1:${captured.port}/callback?access_token=A&refresh_token=R&nonce=WRONG`)).toBe(400);
    await expect(p).rejects.toThrow(/timed out/);
  });

  it("generates a 32+ char url-safe nonce and passes the bound port", async () => {
    let captured!: { port: number; nonce: string };
    const p = runLoopbackLogin({ buildUrl: (port, nonce) => { captured = { port, nonce }; return "http://x"; }, openUrl: async () => true, timeoutMs: 200 });
    await new Promise((r) => setTimeout(r, 20));
    expect(captured.port).toBeGreaterThan(0); expect(captured.nonce).toMatch(/^[A-Za-z0-9_-]{32,}$/);
    await p.catch(() => undefined);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm test` → modules not found.

- [ ] **Step 3: Implement**

```ts
// services/vscode/src/auth/secrets.ts
/** Subset of vscode.SecretStorage so the token manager is testable without the editor. */
export interface SecretStore {
  get(key: string): Promise<string | undefined>;
  store(key: string, value: string): Promise<void>;
  delete(key: string): Promise<void>;
}
export class MemorySecretStore implements SecretStore {
  private m = new Map<string, string>();
  async get(k: string) { return this.m.get(k); }
  async store(k: string, v: string) { this.m.set(k, v); }
  async delete(k: string) { this.m.delete(k); }
}
```

```ts
// services/vscode/src/auth/jwt.ts
export interface AccessClaims { sub?: string; email?: string; role?: string; is_superadmin?: boolean; type?: string; exp?: number }
/** Decode (not verify) a JWT payload. Returns undefined for anything that isn't a 3-part token. */
export function decodeJwtPayload(token: string): AccessClaims | undefined {
  const parts = token.split(".");
  if (parts.length !== 3) return undefined;
  try {
    const json = Buffer.from(parts[1].replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
    const obj = JSON.parse(json);
    return typeof obj === "object" && obj ? (obj as AccessClaims) : undefined;
  } catch { return undefined; }
}
```

```ts
// services/vscode/src/auth/tokenManager.ts
import type { TdtClient, TokenProvider } from "../api/client";
import type { TokenPair } from "../api/types";
import { decodeJwtPayload, type AccessClaims } from "./jwt";
import type { SecretStore } from "./secrets";

export type CredentialKind = "password" | "api_key" | "sso";
interface StoredCredential { kind: CredentialKind; refresh_token?: string; api_key?: string }

/** One per profile. Persists ONLY the long-lived secret (refresh token or API key);
 *  the access token lives in memory and is re-minted on demand. */
export class TokenManager implements TokenProvider {
  private client: TdtClient | undefined;
  private access: string | undefined;
  private cred: StoredCredential | undefined;
  private changeListeners: Array<() => void> = [];
  constructor(private readonly secrets: SecretStore, private readonly profileName: string) {}

  private get key() { return `terraducktel.cred.${this.profileName}`; }
  attach(client: TdtClient) { this.client = client; }
  onDidChange(l: () => void) { this.changeListeners.push(l); return { dispose: () => { this.changeListeners = this.changeListeners.filter((x) => x !== l); } }; }
  private fire() { for (const l of [...this.changeListeners]) l(); }

  async restore(): Promise<void> {
    const raw = await this.secrets.get(this.key);
    this.cred = raw ? (JSON.parse(raw) as StoredCredential) : undefined;
    this.access = this.cred?.kind === "api_key" ? this.cred.api_key : undefined;
  }
  private async persist(c: StoredCredential | undefined) {
    this.cred = c;
    if (c) await this.secrets.store(this.key, JSON.stringify(c)); else await this.secrets.delete(this.key);
    this.fire();
  }

  isSignedIn() { return !!this.cred; }
  kind(): CredentialKind | undefined { return this.cred?.kind; }
  /** Claims from the current access token (undefined for API keys or when signed out). */
  claims(): AccessClaims | undefined { return this.access && this.cred?.kind !== "api_key" ? decodeJwtPayload(this.access) : undefined; }

  async signInWithPassword(email: string, password: string) {
    if (!this.client) throw new Error("TokenManager not attached to a client");
    const pair = await this.client.login(email, password);
    await this.signInWithTokenPair(pair, "password");
  }
  async signInWithTokenPair(pair: TokenPair, kind: "password" | "sso" = "sso") {
    this.access = pair.access_token;
    await this.persist({ kind, refresh_token: pair.refresh_token });
  }
  async signInWithApiKey(key: string) {
    const k = key.trim();
    if (!k.startsWith("tdt_")) throw new Error("That doesn't look like a TDT API key (expected tdt_…)");
    this.access = k;
    await this.persist({ kind: "api_key", api_key: k });
  }
  async signOut() { this.access = undefined; await this.persist(undefined); }

  // ─── TokenProvider ───────────────────────────────────────────────────────
  async getAccessToken() {
    if (!this.cred) return undefined;
    if (this.cred.kind === "api_key") return this.cred.api_key;
    if (!this.access) return this.refreshAccessToken();
    return this.access;
  }
  async refreshAccessToken() {
    if (!this.cred) return undefined;
    if (this.cred.kind === "api_key") return this.cred.api_key;
    if (!this.client || !this.cred.refresh_token) return undefined;
    try {
      const pair = await this.client.refresh(this.cred.refresh_token);
      this.access = pair.access_token;
      await this.persist({ ...this.cred, refresh_token: pair.refresh_token });
      return this.access;
    } catch { return undefined; }
  }
}
```

```ts
// services/vscode/src/auth/profiles.ts
export interface Profile { name: string; url: string; uiUrl?: string; bu?: string; insecureTls?: boolean }

/** Read + normalise `terraducktel.profiles`; malformed entries are dropped (not thrown). */
export function readProfiles(raw: unknown): Profile[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((p) => {
    if (!p || typeof p !== "object") return [];
    const { name, url, uiUrl, bu, insecureTls } = p as Record<string, unknown>;
    if (typeof name !== "string" || !name.trim() || typeof url !== "string" || !/^https?:\/\//.test(url)) return [];
    return [{ name: name.trim(), url: url.replace(/\/+$/, ""), uiUrl: typeof uiUrl === "string" && uiUrl ? uiUrl.replace(/\/+$/, "") : undefined, bu: typeof bu === "string" && bu ? bu : undefined, insecureTls: insecureTls === true }];
  });
}
export function uiUrlFor(p: Profile): string { return p.uiUrl ?? p.url.replace(/\/api$/, ""); }
export function pickActive(profiles: Profile[], activeName: string | undefined): Profile | undefined {
  return profiles.find((p) => p.name === activeName) ?? profiles[0];
}
```

```ts
// services/vscode/src/auth/sso.ts
import * as http from "node:http";
import { randomBytes } from "node:crypto";
import { AddressInfo } from "node:net";
import { URL } from "node:url";
import type { TokenPair } from "../api/types";

export interface LoopbackOptions {
  /** Build the browser URL for a given bound port + nonce (client.ssoLoginUrl). */
  buildUrl: (port: number, nonce: string) => string;
  openUrl: (url: string) => Promise<boolean>;
  timeoutMs?: number;
}

const DONE_HTML = `<!doctype html><meta charset="utf-8"><title>Terraducktel</title><body style="font:14px system-ui;padding:3rem;text-align:center"><p>Signed in to Terraducktel for VS Code.</p><p style="color:#666">You can close this tab.</p></body>`;

/** Mirror of the tdt CLI's browser sign-in: listen on 127.0.0.1:<random>, send the browser
 *  to the API's /auth/oidc/login with cli_port+cli_nonce, and wait for the server's page to
 *  redirect back to /callback?access_token&refresh_token&nonce. The nonce must match. */
export function runLoopbackLogin(opts: LoopbackOptions): Promise<TokenPair> {
  const nonce = randomBytes(24).toString("base64url"); // 32 url-safe chars
  const timeoutMs = opts.timeoutMs ?? 5 * 60_000;
  return new Promise<TokenPair>((resolve, reject) => {
    let settled = false;
    const server = http.createServer((req, res) => {
      const u = new URL(req.url ?? "/", "http://127.0.0.1");
      if (u.pathname !== "/callback") { res.writeHead(404); res.end(); return; }
      const access = u.searchParams.get("access_token"), refresh = u.searchParams.get("refresh_token"), got = u.searchParams.get("nonce");
      if (got !== nonce || !access || !refresh) { res.writeHead(400, { "content-type": "text/plain" }); res.end("Bad sign-in callback (nonce mismatch). Try signing in again."); return; }
      res.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" }); res.end(DONE_HTML);
      finish(() => resolve({ access_token: access, refresh_token: refresh }));
    });
    const timer = setTimeout(() => finish(() => reject(new Error("SSO sign-in timed out waiting for the browser callback"))), timeoutMs);
    function finish(cb: () => void) { if (settled) return; settled = true; clearTimeout(timer); server.close(); cb(); }
    server.on("error", (e) => finish(() => reject(e)));
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address() as AddressInfo;
      opts.openUrl(opts.buildUrl(port, nonce)).then((ok) => { if (!ok) finish(() => reject(new Error("Could not open the browser for SSO sign-in"))); }, (e) => finish(() => reject(e)));
    });
  });
}
```

- [ ] **Step 4: Run tests** — `npm test` → all pass; `npm run typecheck` clean.

- [ ] **Step 5: Commit**

```bash
git add services/vscode/src/auth services/vscode/test/unit/tokenManager.test.ts services/vscode/test/unit/sso.test.ts services/vscode/test/unit/jwt.test.ts
git commit -m "feat(vscode): credential storage, token refresh, profiles and SSO loopback

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 5: Grouping (port of the web tree's path rules) and the polling store

**Files:**
- Create: `services/vscode/src/state/grouping.ts`, `src/state/store.ts`, `test/unit/grouping.test.ts`, `test/unit/store.test.ts`

**Interfaces:**
- Consumes: `Workspace`, `Run` types; `TdtClient.listWorkspaces/listRuns`.
- Produces: `classify(ws) → {cloud: "aws"|"azure"|"gcp"|"other", key, label, region}`, `workspacePathSegments(ws) → {folders, leaf}`, `buildTree(workspaces) → CloudGroup[]` (cloud group → region → FolderNode tree); `Store` with `refresh()`, `workspaces`, `runs`, `runsFor(wsId)`, `onDidChange`, `start(intervalMs)`, `stop()`, `lastError`, `consecutiveFailures`.

- [ ] **Step 1: Failing tests**

```ts
// services/vscode/test/unit/grouping.test.ts
import { describe, expect, it } from "vitest";
import { buildTree, classify, workspacePathSegments } from "../../src/state/grouping";
import type { Workspace } from "../../src/api/types";

const ws = (p: Partial<Workspace> & { name: string }): Workspace => ({
  id: p.name, business_unit_id: "bu", environment: "dev", region: "us-east-1", aws_account_id: "000000000000",
  tf_working_dir: "", repo_ref: "main", kind: "terraform", tags: {}, drift_status: "unknown", path_status: "ok", state_backend: "s3", ...p,
});

describe("classify", () => {
  it("aws by account/region", () => {
    expect(classify(ws({ name: "vpc", aws_account_id: "123456789012", region: "eu-west-1", tf_working_dir: "account-123456789012/eu-west-1/vpc" })))
      .toEqual({ cloud: "aws", key: "123456789012", label: "123456789012", region: "eu-west-1" });
  });
  it("azure by explicit link, region from path", () => {
    const w = ws({ name: "net", aws_account_id: "global", region: "global", azure_subscription_id: "pk1", tf_working_dir: "azure/subscription-11111111-1111-1111-1111-111111111111/westeurope/net" });
    expect(classify(w)).toMatchObject({ cloud: "azure", key: "pk1", region: "westeurope" });
  });
  it("azure by path when unlinked", () => {
    const w = ws({ name: "net", aws_account_id: "global", region: "global", tf_working_dir: "azure/subscription-abc/westeurope/net" });
    expect(classify(w)).toMatchObject({ cloud: "azure", key: "guid:abc", label: "subscription-abc", region: "westeurope" });
  });
  it("gcp by path", () => {
    const w = ws({ name: "gke", aws_account_id: "global", region: "global", tf_working_dir: "gcp/project-acme-prod-1234/us-central1/gke" });
    expect(classify(w)).toMatchObject({ cloud: "gcp", key: "pid:acme-prod-1234", region: "us-central1" });
  });
  it("other providers group under their top folder", () => {
    const w = ws({ name: "dns", aws_account_id: "global", region: "global", tf_working_dir: "cloudflare/tenant-home/dns" });
    expect(classify(w)).toMatchObject({ cloud: "other", key: "other:cloudflare", label: "cloudflare", region: "global" });
  });
});

describe("workspacePathSegments", () => {
  it("strips account/region and returns folders + leaf", () => {
    expect(workspacePathSegments(ws({ name: "w", region: "eu-west-1", tf_working_dir: "account-1/eu-west-1/cust01/worker" }))).toEqual({ folders: ["cust01"], leaf: "worker" });
  });
  it("strips azure and gcp prefixes", () => {
    expect(workspacePathSegments(ws({ name: "w", region: "global", tf_working_dir: "azure/subscription-x/westeurope/team/net" }))).toEqual({ folders: ["team"], leaf: "net" });
    expect(workspacePathSegments(ws({ name: "w", region: "global", tf_working_dir: "gcp/project-p/us-central1/gke" }))).toEqual({ folders: [], leaf: "gke" });
  });
  it("falls back to the name when the path is empty", () => {
    expect(workspacePathSegments(ws({ name: "manual", tf_working_dir: "." }))).toEqual({ folders: [], leaf: "manual" });
  });
});

describe("buildTree", () => {
  it("groups cloud → region → folders → leaves, sorted", () => {
    const tree = buildTree([
      ws({ name: "b", aws_account_id: "1", region: "r1", tf_working_dir: "account-1/r1/b" }),
      ws({ name: "a", aws_account_id: "1", region: "r1", tf_working_dir: "account-1/r1/team/a" }),
      ws({ name: "z", aws_account_id: "1", region: "r2", tf_working_dir: "account-1/r2/z" }),
      ws({ name: "net", aws_account_id: "global", region: "global", tf_working_dir: "azure/subscription-s/westeurope/net" }),
    ]);
    expect(tree.map((g) => [g.cloud, g.key])).toEqual([["aws", "1"], ["azure", "guid:s"]]);
    const r1 = tree[0].regions.find((r) => r.region === "r1")!;
    expect([...r1.root.folders.keys()]).toEqual(["team"]);
    expect(r1.root.workspaces.map((w) => w.leaf)).toEqual(["b"]);
    expect(r1.root.folders.get("team")!.workspaces.map((w) => w.leaf)).toEqual(["a"]);
  });
  it("folds a bare workspace into a same-named folder", () => {
    const tree = buildTree([
      ws({ name: "tools", aws_account_id: "1", region: "r", tf_working_dir: "account-1/r/tools" }),
      ws({ name: "agent", aws_account_id: "1", region: "r", tf_working_dir: "account-1/r/tools/agent" }),
    ]);
    const root = tree[0].regions[0].root;
    expect(root.workspaces).toEqual([]);
    expect(root.folders.get("tools")!.workspaces.map((w) => w.leaf).sort()).toEqual(["agent", "tools"]);
  });
});
```

```ts
// services/vscode/test/unit/store.test.ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import { Store } from "../../src/state/store";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", signOut: async () => {} };

describe("Store", () => {
  let srv: FakeServer; let url: string;
  beforeEach(async () => { srv = new FakeServer(); url = await srv.start(); });
  afterEach(async () => { await srv.stop(); vi.useRealTimers(); });

  it("refresh loads workspaces + runs and indexes runs by workspace", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, [{ id: "w1", name: "a" }]);
    srv.json("GET", "/api/v1/runs", 200, [{ id: "r2", workspace_id: "w1", status: "planned", created_at: "2026-01-02" }, { id: "r1", workspace_id: "w1", status: "failed", created_at: "2026-01-01" }]);
    const s = new Store(() => new TdtClient({ baseUrl: url, bu: "default", tokens }), { runsLimit: 50 });
    let events = 0; s.onDidChange(() => events++);
    await s.refresh();
    expect(s.workspaces.map((w) => w.id)).toEqual(["w1"]);
    expect(s.runsFor("w1").map((r) => r.id)).toEqual(["r2", "r1"]);
    expect(srv.calls.find((c) => c.url.startsWith("/api/v1/runs"))!.url).toBe("/api/v1/runs?limit=50");
    expect(events).toBe(1); expect(s.lastError).toBeUndefined();
  });

  it("keeps the last good data and counts failures", async () => {
    srv.json("GET", "/api/v1/workspaces", 200, [{ id: "w1", name: "a" }]); srv.json("GET", "/api/v1/runs", 200, []);
    const s = new Store(() => new TdtClient({ baseUrl: url, bu: "default", tokens }), { runsLimit: 50 });
    await s.refresh();
    await srv.stop(); // server gone
    await s.refresh();
    expect(s.workspaces.length).toBe(1); expect(s.consecutiveFailures).toBe(1); expect(s.lastError).toBeDefined();
    srv = new FakeServer(); await srv.start(); // afterEach stops this one
  });

  it("does not overlap polls", async () => {
    let inflight = 0, max = 0;
    srv.on("GET", "/api/v1/workspaces", (_q, _b, res) => { inflight++; max = Math.max(max, inflight); setTimeout(() => { inflight--; res.writeHead(200, { "content-type": "application/json" }); res.end("[]"); }, 50); });
    srv.json("GET", "/api/v1/runs", 200, []);
    const s = new Store(() => new TdtClient({ baseUrl: url, bu: "default", tokens }), { runsLimit: 50 });
    await Promise.all([s.refresh(), s.refresh(), s.refresh()]);
    expect(max).toBe(1);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm test` → modules not found.

- [ ] **Step 3: Implement**

```ts
// services/vscode/src/state/grouping.ts
// Port of services/ui/src/components/workspace-tree/paths.ts (grouping rules only).
// Keep the two in step: same fixtures live in grouping.test.ts and paths.test.ts.
import type { Workspace } from "../api/types";

export type Cloud = "aws" | "azure" | "gcp" | "other";
export interface Classification { cloud: Cloud; key: string; label: string; region: string }

const parts = (ws: Workspace) => (ws.tf_working_dir ?? "").trim().split("/").filter(Boolean);

export function azureInfo(ws: Workspace): { guid: string; region: string } | null {
  const p = parts(ws); if (p[0]?.toLowerCase() !== "azure") return null;
  const m = (p[1] ?? "").match(/^subscription-(.+)$/); if (!m) return null;
  return { guid: m[1], region: p[2] ?? ws.region };
}
export function gcpInfo(ws: Workspace): { projectId: string; region: string } | null {
  const p = parts(ws); if (p[0]?.toLowerCase() !== "gcp") return null;
  const m = (p[1] ?? "").match(/^project-(.+)$/); if (!m) return null;
  return { projectId: m[1], region: p[2] ?? ws.region };
}

/** Which top-level group a workspace belongs to. Explicit links win over path detection;
 *  AWS is the default only when an account id is set; everything else groups by its top folder. */
export function classify(ws: Workspace): Classification {
  if (ws.azure_subscription_id) { const i = azureInfo(ws); return { cloud: "azure", key: ws.azure_subscription_id, label: i ? `subscription-${i.guid}` : "Azure subscription", region: i?.region ?? ws.region }; }
  if (ws.gcp_project_id) { const i = gcpInfo(ws); return { cloud: "gcp", key: ws.gcp_project_id, label: i ? `project-${i.projectId}` : "GCP project", region: i?.region ?? ws.region }; }
  const a = azureInfo(ws); if (a) return { cloud: "azure", key: `guid:${a.guid}`, label: `subscription-${a.guid}`, region: a.region };
  const g = gcpInfo(ws); if (g) return { cloud: "gcp", key: `pid:${g.projectId}`, label: `project-${g.projectId}`, region: g.region };
  if (ws.aws_account_id && ws.aws_account_id !== "global") return { cloud: "aws", key: ws.aws_account_id, label: ws.aws_account_id, region: ws.region };
  const top = parts(ws)[0] ?? "other";
  return { cloud: "other", key: `other:${top}`, label: top, region: ws.region || "global" };
}

export function workspacePathSegments(ws: Workspace): { folders: string[]; leaf: string } {
  const raw = (ws.tf_working_dir ?? "").trim();
  if (!raw || raw === ".") return { folders: [], leaf: ws.name };
  const p = raw.split("/").filter(Boolean);
  if (p[0]?.startsWith("account-")) p.shift();
  let regionToStrip = ws.region;
  const head = p[0]?.toLowerCase();
  if ((head === "azure" && /^subscription-/.test(p[1] ?? "")) || (head === "gcp" && /^project-/.test(p[1] ?? ""))) { p.shift(); p.shift(); regionToStrip = p[0] ?? ws.region; }
  if (p[0] === regionToStrip) p.shift();
  if (p.length === 0) return { folders: [], leaf: ws.name };
  const leaf = p.pop() as string;
  return { folders: p, leaf };
}

export interface FolderNode { name: string; folders: Map<string, FolderNode>; workspaces: Array<{ ws: Workspace; leaf: string }> }
export interface RegionGroup { region: string; root: FolderNode; count: number }
export interface CloudGroup { cloud: Cloud; key: string; label: string; regions: RegionGroup[]; count: number }

function buildFolderTree(workspaces: Workspace[]): FolderNode {
  const root: FolderNode = { name: "", folders: new Map(), workspaces: [] };
  const items = workspaces.map((ws) => ({ ws, ...workspacePathSegments(ws) }));
  for (const { folders } of items) { let cur = root; for (const seg of folders) { let n = cur.folders.get(seg); if (!n) { n = { name: seg, folders: new Map(), workspaces: [] }; cur.folders.set(seg, n); } cur = n; } }
  for (const { ws, folders, leaf } of items) {
    let cur = root; for (const seg of folders) cur = cur.folders.get(seg)!;
    const colliding = cur.folders.get(leaf);
    (colliding ?? cur).workspaces.push({ ws, leaf });
  }
  const sortNode = (n: FolderNode) => { n.workspaces.sort((a, b) => a.leaf.localeCompare(b.leaf)); n.folders = new Map([...n.folders.entries()].sort(([a], [b]) => a.localeCompare(b))); for (const c of n.folders.values()) sortNode(c); };
  sortNode(root);
  return root;
}
export function countNode(n: FolderNode): number { let c = n.workspaces.length; for (const f of n.folders.values()) c += countNode(f); return c; }

const CLOUD_ORDER: Cloud[] = ["aws", "azure", "gcp", "other"];
export function buildTree(workspaces: Workspace[]): CloudGroup[] {
  const groups = new Map<string, { cls: Classification; byRegion: Map<string, Workspace[]> }>();
  for (const ws of workspaces) {
    const cls = classify(ws); const gk = `${cls.cloud}|${cls.key}`;
    const g = groups.get(gk) ?? { cls, byRegion: new Map() }; groups.set(gk, g);
    const list = g.byRegion.get(cls.region) ?? []; list.push(ws); g.byRegion.set(cls.region, list);
  }
  return [...groups.values()]
    .map(({ cls, byRegion }) => {
      const regions = [...byRegion.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([region, list]) => { const root = buildFolderTree(list); return { region, root, count: countNode(root) }; });
      return { cloud: cls.cloud, key: cls.key, label: cls.label, regions, count: regions.reduce((s, r) => s + r.count, 0) };
    })
    .sort((a, b) => CLOUD_ORDER.indexOf(a.cloud) - CLOUD_ORDER.indexOf(b.cloud) || a.label.localeCompare(b.label));
}
```

```ts
// services/vscode/src/state/store.ts
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
```

- [ ] **Step 4: Run tests** — `npm test` all green; `npm run typecheck` clean.

- [ ] **Step 5: Commit**

```bash
git add services/vscode/src/state services/vscode/test/unit/grouping.test.ts services/vscode/test/unit/store.test.ts
git commit -m "feat(vscode): workspace grouping (web tree rules) and polling store

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 6: Tree views (Workspaces, Runs) and the session wiring

**Files:**
- Create: `services/vscode/src/views/nodes.ts`, `src/views/workspacesTree.ts`, `src/views/runsTree.ts`, `src/session.ts`, `test/unit/nodes.test.ts`
- Modify: `src/extension.ts` (real activation: session + views + auth commands + refresh/switch BU; run commands land in Task 7)

**Interfaces:**
- Consumes: `Store`, `buildTree`, `TokenManager`, `TdtClient`, `Profile` helpers.
- Produces: `Session` (holds active profile, `TokenManager`, `TdtClient`, `Store`, current BU; `onDidChange`; `ensureSignedIn()`; `setBu(slug)`; `signIn(mode)`; `signOut()`; sets the `terraducktel.signedIn` / `terraducktel.canWrite` contexts); node classes with `contextValue`s `cloud`, `region`, `folder`, `workspace`, `run.<status>`; `statusIcon(status) → ThemeIcon`; `describeRun(run)`.

- [ ] **Step 1: Failing test for node rendering (pure part)**

```ts
// services/vscode/test/unit/nodes.test.ts
import { describe, expect, it } from "vitest";
import { describeRun, runContextValue, statusIconId, workspaceDescription } from "../../src/views/nodes";

describe("node rendering helpers", () => {
  it("maps statuses to icons", () => {
    expect(statusIconId("applied")).toBe("pass"); expect(statusIconId("failed")).toBe("error");
    expect(statusIconId("awaiting_approval")).toBe("bell"); expect(statusIconId("running")).toBe("sync~spin");
    expect(statusIconId("planned")).toBe("check"); expect(statusIconId("weird")).toBe("circle-outline");
  });
  it("builds run context values and labels", () => {
    expect(runContextValue({ id: "r", workspace_id: "w", command: "apply", status: "awaiting_approval" })).toBe("run.awaiting_approval");
    expect(describeRun({ id: "abcdef123456", workspace_id: "w", command: "plan", status: "planned", branch: "main", created_at: "2026-09-12T10:00:00Z" })).toMatch(/^plan · planned · main · abcdef12/);
  });
  it("describes a workspace with drift and branch", () => {
    expect(workspaceDescription({ repo_ref: "feat/x", drift_status: "drifted" } as never, { status: "failed" } as never)).toBe("failed · feat/x · drift");
    expect(workspaceDescription({ repo_ref: "main", drift_status: "clean" } as never, undefined)).toBe("no runs · main");
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm test` → module not found.

- [ ] **Step 3: Implement**

```ts
// services/vscode/src/views/nodes.ts
import * as vscode from "vscode";
import type { Run, Workspace } from "../api/types";
import type { CloudGroup, FolderNode, RegionGroup } from "../state/grouping";

export function statusIconId(status: string): string {
  switch (status) {
    case "applied": return "pass";
    case "planned": return "check";
    case "awaiting_approval": return "bell";
    case "failed": return "error";
    case "cancelled": return "circle-slash";
    case "pending": return "clock";
    case "running": case "planning": case "applying": return "sync~spin";
    default: return "circle-outline";
  }
}
export function statusIcon(status: string): vscode.ThemeIcon {
  const color = status === "failed" ? "testing.iconFailed" : status === "applied" || status === "planned" ? "testing.iconPassed" : status === "awaiting_approval" ? "notificationsWarningIcon.foreground" : undefined;
  return new vscode.ThemeIcon(statusIconId(status), color ? new vscode.ThemeColor(color) : undefined);
}
export function runContextValue(run: Pick<Run, "status">): string { return `run.${run.status}`; }
export function describeRun(run: Run): string {
  const when = run.created_at ? new Date(run.created_at).toLocaleString() : "";
  return [run.command, run.status, run.branch ?? "", run.id.slice(0, 8), when].filter(Boolean).join(" · ");
}
export function workspaceDescription(ws: Workspace, last: Run | undefined): string {
  const bits = [last ? last.status : "no runs", ws.repo_ref];
  if (ws.drift_status === "drifted") bits.push("drift");
  return bits.join(" · ");
}
const CLOUD_ICON: Record<string, string> = { aws: "cloud", azure: "azure", gcp: "globe", other: "server-environment" };

export type Node = CloudNode | RegionNode | FolderTreeNode | WorkspaceNode | RunNode | MessageNode;
export class CloudNode extends vscode.TreeItem { constructor(public group: CloudGroup) { super(`${group.label}`, vscode.TreeItemCollapsibleState.Collapsed); this.description = `${group.cloud.toUpperCase()} · ${group.count}`; this.contextValue = "cloud"; this.iconPath = new vscode.ThemeIcon(CLOUD_ICON[group.cloud] ?? "cloud"); this.id = `cloud:${group.cloud}:${group.key}`; } }
export class RegionNode extends vscode.TreeItem { constructor(public group: CloudGroup, public region: RegionGroup) { super(region.region, vscode.TreeItemCollapsibleState.Expanded); this.description = String(region.count); this.contextValue = "region"; this.iconPath = new vscode.ThemeIcon("location"); this.id = `region:${group.cloud}:${group.key}:${region.region}`; } }
export class FolderTreeNode extends vscode.TreeItem { constructor(public folder: FolderNode, public path: string) { super(folder.name, vscode.TreeItemCollapsibleState.Expanded); this.contextValue = "folder"; this.iconPath = vscode.ThemeIcon.Folder; this.id = `folder:${path}`; } }
export class WorkspaceNode extends vscode.TreeItem {
  constructor(public ws: Workspace, leaf: string, last: Run | undefined, runCount: number) {
    super(leaf, runCount ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None);
    this.id = `ws:${ws.id}`; this.contextValue = "workspace"; this.description = workspaceDescription(ws, last);
    this.iconPath = last ? statusIcon(last.status) : new vscode.ThemeIcon("circle-outline");
    const md = new vscode.MarkdownString(); md.appendMarkdown(`**${ws.name}**  \n\`${ws.tf_working_dir}\`  \nid \`${ws.id}\`  \nenv ${ws.environment} · kind ${ws.kind} · branch ${ws.repo_ref} · drift ${ws.drift_status} · path ${ws.path_status}`);
    if (ws.repo_url) md.appendMarkdown(`  \nrepo ${ws.repo_url}`);
    const tags = Object.entries(ws.tags ?? {}); if (tags.length) md.appendMarkdown(`  \ntags ${tags.map(([k, v]) => `${k}=${v}`).join(", ")}`);
    this.tooltip = md;
  }
}
export class RunNode extends vscode.TreeItem {
  constructor(public run: Run, opts: { showWorkspace?: string } = {}) {
    super(opts.showWorkspace ? `${opts.showWorkspace} · ${run.command}` : run.command, vscode.TreeItemCollapsibleState.None);
    this.id = `run:${run.id}`; this.contextValue = runContextValue(run); this.description = describeRun(run).replace(/^[^·]+· /, "");
    this.iconPath = statusIcon(run.status); this.tooltip = `${run.command} ${run.status}\n${run.id}\nbranch ${run.branch ?? "-"}\ncreated ${run.created_at ?? "-"}`;
    this.command = { command: "terraducktel.watchRun", title: "Watch run", arguments: [this] };
  }
}
export class MessageNode extends vscode.TreeItem { constructor(msg: string, icon = "info") { super(msg, vscode.TreeItemCollapsibleState.None); this.contextValue = "message"; this.iconPath = new vscode.ThemeIcon(icon); } }
```

```ts
// services/vscode/src/session.ts
import * as vscode from "vscode";
import { TdtClient } from "./api/client";
import { TokenManager } from "./auth/tokenManager";
import { pickActive, readProfiles, uiUrlFor, type Profile } from "./auth/profiles";
import { runLoopbackLogin } from "./auth/sso";
import { Store } from "./state/store";
import { CTX_CAN_WRITE, CTX_SIGNED_IN } from "./ids";

/** Everything that depends on "which deployment / which BU / who am I". Rebuilt on profile change. */
export class Session implements vscode.Disposable {
  profile: Profile | undefined;
  tokens: TokenManager | undefined;
  client: TdtClient | undefined;
  bu = "";
  readonly store: Store;
  private changed = new vscode.EventEmitter<void>();
  readonly onDidChange = this.changed.event;
  private disposables: vscode.Disposable[] = [];
  readonly log: vscode.OutputChannel;

  constructor(private readonly ctx: vscode.ExtensionContext) {
    this.log = vscode.window.createOutputChannel("Terraducktel");
    this.store = new Store(() => this.client, { runsLimit: this.cfg().get<number>("runsLimit", 200) });
    this.disposables.push(this.log, this.store,
      vscode.workspace.onDidChangeConfiguration((e) => { if (e.affectsConfiguration("terraducktel")) void this.reload(); }));
  }
  private cfg() { return vscode.workspace.getConfiguration("terraducktel"); }
  uiUrl() { return this.profile ? uiUrlFor(this.profile) : undefined; }
  canWrite(): boolean {
    const c = this.tokens?.claims();
    if (!this.tokens?.isSignedIn()) return false;
    if (!c) return true;                      // API key: role unknown; server enforces
    return c.is_superadmin === true || c.role === "operator" || c.role === "admin";
  }

  async reload(): Promise<void> {
    const profiles = readProfiles(this.cfg().get("profiles"));
    const next = pickActive(profiles, this.cfg().get<string>("activeProfile"));
    this.profile = next;
    if (!next) { this.tokens = undefined; this.client = undefined; this.bu = ""; this.store.clear(); await this.publishContexts(); return; }
    this.bu = this.ctx.workspaceState.get<string>(`bu.${next.name}`) ?? next.bu ?? "";
    this.tokens = new TokenManager(this.ctx.secrets, next.name);
    await this.tokens.restore();
    this.client = new TdtClient({ baseUrl: next.url, bu: this.bu, tokens: this.tokens, insecureTls: next.insecureTls, trace: (l) => { if (this.cfg().get<boolean>("trace")) this.log.appendLine(l); } });
    this.tokens.attach(this.client);
    this.client.onSignedOut(() => { void vscode.window.showWarningMessage("Terraducktel: session expired — sign in again.", "Sign in").then((a) => a && vscode.commands.executeCommand("terraducktel.signIn")); void this.publishContexts(); });
    this.tokens.onDidChange(() => void this.publishContexts());
    await this.publishContexts();
    this.store.start(Math.max(5, this.cfg().get<number>("refreshIntervalSeconds", 30)) * 1000);
    void this.store.refresh();
  }
  private async publishContexts() {
    await vscode.commands.executeCommand("setContext", CTX_SIGNED_IN, !!this.tokens?.isSignedIn());
    await vscode.commands.executeCommand("setContext", CTX_CAN_WRITE, this.canWrite());
    this.changed.fire();
  }

  async setBu(slug: string) {
    if (!this.profile || !this.client) return;
    this.bu = slug; await this.ctx.workspaceState.update(`bu.${this.profile.name}`, slug);
    this.client = this.client.withBu(slug); this.tokens?.attach(this.client);
    this.changed.fire(); await this.store.refresh();
  }

  /** Throws a friendly error when there is no profile / no session. */
  requireClient(): TdtClient {
    if (!this.profile) throw new Error("No Terraducktel profile configured. Add one under Settings → Terraducktel → Profiles.");
    if (!this.client || !this.tokens?.isSignedIn()) throw new Error("Not signed in to Terraducktel. Run “Terraducktel: Sign in”.");
    return this.client;
  }

  async signIn(): Promise<void> {
    if (!this.profile || !this.client || !this.tokens) throw new Error("Add a profile under Settings → Terraducktel → Profiles first.");
    const cfg = await this.client.authConfig().catch(() => ({ mode: "local", oidc_enabled: false, cli_loopback: false }));
    const items: Array<vscode.QuickPickItem & { mode: "sso" | "password" | "api_key" }> = [];
    if (cfg.oidc_enabled && cfg.cli_loopback) items.push({ label: "$(globe) Sign in with SSO", description: cfg.oidc_issuer ?? "", mode: "sso" });
    if (cfg.mode !== "oidc") items.push({ label: "$(key) Email + password", mode: "password" });
    items.push({ label: "$(lock) API key (tdt_…)", description: "long-lived, for automation", mode: "api_key" });
    const pick = items.length === 1 ? items[0] : await vscode.window.showQuickPick(items, { placeHolder: `Sign in to ${this.profile.name} (${this.profile.url})` });
    if (!pick) return;
    if (pick.mode === "password") {
      const email = await vscode.window.showInputBox({ prompt: "Email", ignoreFocusOut: true }); if (!email) return;
      const pw = await vscode.window.showInputBox({ prompt: "Password", password: true, ignoreFocusOut: true }); if (pw === undefined) return;
      await this.tokens.signInWithPassword(email, pw);
    } else if (pick.mode === "api_key") {
      const key = await vscode.window.showInputBox({ prompt: "API key (tdt_…)", password: true, ignoreFocusOut: true }); if (!key) return;
      await this.tokens.signInWithApiKey(key);
    } else {
      if (vscode.env.remoteName) throw new Error("SSO sign-in needs a browser on this machine; in a remote session use an API key instead.");
      const client = this.client;
      const pair = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Terraducktel: complete sign-in in your browser…", cancellable: false },
        () => runLoopbackLogin({ buildUrl: (port, nonce) => client.ssoLoginUrl(port, nonce), openUrl: (u) => vscode.env.openExternal(vscode.Uri.parse(u)) as Promise<boolean> }));
      await this.tokens.signInWithTokenPair(pair, "sso");
    }
    await this.publishContexts();
    await this.store.refresh();
    const who = this.tokens.claims()?.email ?? (this.tokens.kind() === "api_key" ? "API key" : "user");
    void vscode.window.showInformationMessage(`Terraducktel: signed in to ${this.profile.name} as ${who}.`);
  }
  async signOut() { await this.tokens?.signOut(); this.store.clear(); await this.publishContexts(); }
  dispose() { for (const d of this.disposables) d.dispose(); this.changed.dispose(); }
}
```

```ts
// services/vscode/src/views/workspacesTree.ts
import * as vscode from "vscode";
import type { Session } from "../session";
import { buildTree, type CloudGroup, type FolderNode } from "../state/grouping";
import { CloudNode, FolderTreeNode, MessageNode, RegionNode, RunNode, WorkspaceNode, type Node } from "./nodes";

export class WorkspacesTree implements vscode.TreeDataProvider<Node> {
  private changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private tree: CloudGroup[] = [];
  constructor(private readonly s: Session) {
    s.store.onDidChange(() => { this.tree = buildTree(s.store.workspaces); this.changed.fire(undefined); });
    s.onDidChange(() => this.changed.fire(undefined));
  }
  getTreeItem(n: Node) { return n; }
  getChildren(n?: Node): Node[] {
    if (!n) {
      if (!this.s.profile) return [new MessageNode("No profile configured — Settings → Terraducktel", "gear")];
      if (!this.s.tokens?.isSignedIn()) return [];
      const head: Node[] = [];
      if (this.s.store.lastError) head.push(new MessageNode(`Refresh failed: ${this.s.store.lastError.message}`, "warning"));
      if (this.s.profile.insecureTls) head.push(new MessageNode("TLS verification is OFF for this profile", "shield"));
      if (!this.tree.length && !this.s.store.lastError) head.push(new MessageNode(`No workspaces in BU “${this.s.bu || "default"}”`));
      return [...head, ...this.tree.map((g) => new CloudNode(g))];
    }
    if (n instanceof CloudNode) return n.group.regions.map((r) => new RegionNode(n.group, r));
    if (n instanceof RegionNode) return this.folderChildren(n.region.root, `${n.group.key}/${n.region.region}`);
    if (n instanceof FolderTreeNode) return this.folderChildren(n.folder, n.path);
    if (n instanceof WorkspaceNode) return this.s.store.runsFor(n.ws.id).slice(0, 10).map((r) => new RunNode(r));
    return [];
  }
  private folderChildren(f: FolderNode, path: string): Node[] {
    const folders = [...f.folders.values()].map((c) => new FolderTreeNode(c, `${path}/${c.name}`));
    const leaves = f.workspaces.map(({ ws, leaf }) => { const runs = this.s.store.runsFor(ws.id); return new WorkspaceNode(ws, leaf, runs[0], runs.length); });
    return [...folders, ...leaves];
  }
  getParent(): undefined { return undefined; }
}
```

```ts
// services/vscode/src/views/runsTree.ts
import * as vscode from "vscode";
import type { Session } from "../session";
import { MessageNode, RunNode, type Node } from "./nodes";

const ORDER = ["awaiting_approval", "applying", "running", "planning", "pending", "planned", "failed", "applied", "cancelled"];

export class RunsTree implements vscode.TreeDataProvider<Node> {
  private changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  constructor(private readonly s: Session) { s.store.onDidChange(() => this.changed.fire(undefined)); s.onDidChange(() => this.changed.fire(undefined)); }
  getTreeItem(n: Node) { return n; }
  getChildren(n?: Node): Node[] {
    if (n || !this.s.tokens?.isSignedIn()) return [];
    const runs = [...this.s.store.runs].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status) || (b.created_at ?? "").localeCompare(a.created_at ?? ""));
    if (!runs.length) return [new MessageNode("No runs yet")];
    const name = (id: string) => this.s.store.workspace(id)?.name ?? id.slice(0, 8);
    return runs.map((r) => new RunNode(r, { showWorkspace: name(r.workspace_id) }));
  }
}
```

```ts
// services/vscode/src/extension.ts  (Task 6 version; Task 7 adds registerRunCommands)
import * as vscode from "vscode";
import { registerAuthCommands } from "./commands/auth";
import { Session } from "./session";
import { RunsTree } from "./views/runsTree";
import { WorkspacesTree } from "./views/workspacesTree";
import { VIEW_RUNS, VIEW_WORKSPACES } from "./ids";

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const session = new Session(context);
  context.subscriptions.push(session);
  const wsTree = new WorkspacesTree(session), runsTree = new RunsTree(session);
  const wsView = vscode.window.createTreeView(VIEW_WORKSPACES, { treeDataProvider: wsTree, showCollapseAll: true });
  const runsView = vscode.window.createTreeView(VIEW_RUNS, { treeDataProvider: runsTree });
  context.subscriptions.push(wsView, runsView);
  const onVis = () => { if (wsView.visible || runsView.visible) void session.store.refresh(); };
  context.subscriptions.push(wsView.onDidChangeVisibility(onVis), runsView.onDidChangeVisibility(onVis));
  session.store.onDidChange(() => { const n = session.store.runs.filter((r) => r.status === "awaiting_approval").length; runsView.badge = n ? { value: n, tooltip: `${n} run(s) awaiting approval` } : undefined; });
  registerAuthCommands(context, session);
  await session.reload();
}
export function deactivate(): void {}
```

```ts
// services/vscode/src/commands/auth.ts
import * as vscode from "vscode";
import type { Session } from "../session";
import { readProfiles } from "../auth/profiles";

export function wrap(fn: (...a: unknown[]) => Promise<unknown>) {
  return async (...a: unknown[]) => { try { await fn(...a); } catch (e) { void vscode.window.showErrorMessage(`Terraducktel: ${e instanceof Error ? e.message : String(e)}`); } };
}

export function registerAuthCommands(ctx: vscode.ExtensionContext, s: Session) {
  ctx.subscriptions.push(
    vscode.commands.registerCommand("terraducktel.signIn", wrap(() => s.signIn())),
    vscode.commands.registerCommand("terraducktel.signOut", wrap(async () => { await s.signOut(); void vscode.window.showInformationMessage("Terraducktel: signed out."); })),
    vscode.commands.registerCommand("terraducktel.refresh", wrap(() => s.store.refresh())),
    vscode.commands.registerCommand("terraducktel.switchProfile", wrap(async () => {
      const profiles = readProfiles(vscode.workspace.getConfiguration("terraducktel").get("profiles"));
      if (!profiles.length) { void vscode.commands.executeCommand("workbench.action.openSettings", "terraducktel.profiles"); return; }
      const pick = await vscode.window.showQuickPick(profiles.map((p) => ({ label: p.name, description: p.url })), { placeHolder: "Active Terraducktel profile" });
      if (pick) await vscode.workspace.getConfiguration("terraducktel").update("activeProfile", pick.label, vscode.ConfigurationTarget.Global);
    })),
    vscode.commands.registerCommand("terraducktel.switchBusinessUnit", wrap(async () => {
      const c = s.requireClient();
      if (s.tokens?.kind() === "api_key") throw new Error("API keys are bound to one business unit.");
      const bus = await c.listBusinessUnits();
      const items = bus.map((b) => ({ label: b.slug, description: b.name }));
      if (s.tokens?.claims()?.is_superadmin) items.unshift({ label: "all", description: "every business unit (superadmin)" });
      const pick = await vscode.window.showQuickPick(items, { placeHolder: `Business unit (current: ${s.bu || "default"})` });
      if (pick) await s.setBu(pick.label);
    })),
  );
}
```

- [ ] **Step 4: Verify** — `npm test` (nodes test passes), `npm run typecheck`, `npm run build`. Then launch an Extension Development Host manually is NOT required here; Task 8's integration smoke covers activation.

- [ ] **Step 5: Commit**

```bash
git add services/vscode/src services/vscode/test/unit/nodes.test.ts
git commit -m "feat(vscode): session, sign-in flows, workspaces and runs tree views

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 7: Run output tailing, plan document, run and workspace commands

**Files:**
- Create: `services/vscode/src/output/runOutput.ts`, `src/output/planDocument.ts`, `src/commands/run.ts`, `src/commands/workspace.ts`, `test/unit/runOutput.test.ts`, `test/unit/planDocument.test.ts`
- Modify: `src/extension.ts` (register the plan document provider and the two command groups)

**Interfaces:**
- Consumes: `TdtClient.getRun/getSteps/getPlan/getGraph/approve/reject/cancel/triggerRun/updateWorkspace/listBranches/syncWorkspace`, `Session`, nodes.
- Produces: `tailRun(client, runId, sink, opts)` (pure async loop, testable), `RunOutputManager.watch(runId, title)`; `PlanDocumentProvider` for scheme `tdt-plan` + `planLineKinds(text) → ("add"|"change"|"destroy"|"replace"|"other")[]`; commands `terraducktel.plan/apply/destroy/setBranch/syncWorkspace/openInBrowser/copyId/watchRun/showPlan/approve/reject/cancelRun`.

- [ ] **Step 1: Failing tests for the pure parts**

```ts
// services/vscode/test/unit/runOutput.test.ts
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FakeServer } from "../fake-server";
import { TdtClient } from "../../src/api/client";
import { tailRun } from "../../src/output/runOutput";

const tokens = { getAccessToken: async () => "t", refreshAccessToken: async () => "t", signOut: async () => {} };

describe("tailRun", () => {
  let srv: FakeServer; let url: string;
  beforeEach(async () => { srv = new FakeServer(); url = await srv.start(); });
  afterEach(async () => { await srv.stop(); });

  it("appends only new steps using the since cursor and stops at a terminal status", async () => {
    let poll = 0;
    srv.on("GET", "/api/v1/runs/r1", (_q, _b, res) => { poll++; res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify({ id: "r1", workspace_id: "w", command: "plan", status: poll < 3 ? "running" : "planned" })); });
    srv.on("GET", "/api/v1/runs/r1/steps", (req, _b, res) => {
      const since = Number(new URL(req.url!, "http://x").searchParams.get("since") ?? "0");
      const all = [{ position: 0, name: "Init", status: "success", output: "ok\n" }, { position: 1, name: "Plan", status: poll < 3 ? "running" : "success", output: poll < 3 ? "planning…" : "planning…\nNo changes." }];
      res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(all.filter((s) => s.position >= since)));
    });
    const lines: string[] = [];
    const final = await tailRun(new TdtClient({ baseUrl: url, bu: "b", tokens }), "r1", { appendLine: (l) => lines.push(l) }, { pollMs: 5 });
    expect(final.status).toBe("planned");
    expect(lines.filter((l) => l.includes("── Init")).length).toBe(1);          // header printed once
    expect(lines.join("\n")).toContain("No changes.");
    expect(lines.filter((l) => l === "ok").length).toBe(1);                      // finished step output not repeated
    const sinces = srv.requests("GET", "/api/v1/runs/r1/steps").map((c) => new URL(c.url, "http://x").searchParams.get("since"));
    expect(sinces[0]).toBe("0"); expect(sinces.slice(1).every((s) => s === "1")).toBe(true); // cursor stays on the unfinished step
  });

  it("honours cancellation", async () => {
    srv.json("GET", "/api/v1/runs/r1", 200, { id: "r1", workspace_id: "w", command: "plan", status: "running" });
    srv.json("GET", "/api/v1/runs/r1/steps", 200, []);
    let cancelled = false; setTimeout(() => (cancelled = true), 30);
    const final = await tailRun(new TdtClient({ baseUrl: url, bu: "b", tokens }), "r1", { appendLine: () => {} }, { pollMs: 5, isCancelled: () => cancelled });
    expect(final.status).toBe("running");
  });
});
```

```ts
// services/vscode/test/unit/planDocument.test.ts
import { describe, expect, it } from "vitest";
import { planLineKinds } from "../../src/output/planDocument";
describe("planLineKinds", () => {
  it("classifies terraform plan lines", () => {
    const text = ["Terraform will perform the following actions:", "  # aws_s3_bucket.b will be created", "  + resource \"aws_s3_bucket\" \"b\" {", "      + bucket = \"x\"", "  ~ update in-place", "  - resource \"x\" \"y\" {", "-/+ resource \"a\" \"b\" (replace)", "Plan: 1 to add, 1 to change, 1 to destroy."].join("\n");
    expect(planLineKinds(text)).toEqual(["other", "other", "add", "add", "change", "destroy", "replace", "other"]);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm test` → modules not found.

- [ ] **Step 3: Implement**

```ts
// services/vscode/src/output/runOutput.ts
import * as vscode from "vscode";
import type { TdtClient } from "../api/client";
import { PLAN_LANDED_STATUSES, type Run, type RunStep } from "../api/types";

export interface LineSink { appendLine(line: string): void }
export interface TailOptions { pollMs?: number; isCancelled?: () => boolean; timeoutMs?: number }

/** Follow a run: print each step once (header + output), re-poll only from the first
 *  unfinished step (`since`), stop when the run lands (terminal or awaiting_approval). */
export async function tailRun(client: TdtClient, runId: string, sink: LineSink, opts: TailOptions = {}): Promise<Run> {
  const pollMs = opts.pollMs ?? 2000, deadline = Date.now() + (opts.timeoutMs ?? 3 * 60 * 60_000);
  let since = 0; const printed = new Map<number, number>(); // position → chars of output already printed
  let run = await client.getRun(runId);
  for (;;) {
    const steps: RunStep[] = await client.getSteps(runId, since);
    for (const st of steps.sort((a, b) => a.position - b.position)) {
      if (!printed.has(st.position)) { sink.appendLine(`── ${st.name} [${st.status}]`); printed.set(st.position, 0); }
      const out = st.output ?? "", done = printed.get(st.position)!;
      if (out.length > done) { for (const l of out.slice(done).replace(/\n$/, "").split("\n")) sink.appendLine(l); printed.set(st.position, out.length); }
      if (st.status !== "pending" && st.status !== "running") { if (st.position === since) since = st.position + 1; }
    }
    // advance `since` past any contiguous finished prefix
    while ([...steps].some((s) => s.position === since && s.status !== "pending" && s.status !== "running")) since++;
    run = await client.getRun(runId);
    if (PLAN_LANDED_STATUSES.has(run.status)) { sink.appendLine(`── run ${run.status}`); return run; }
    if (opts.isCancelled?.() || Date.now() > deadline) return run;
    await new Promise((r) => setTimeout(r, pollMs));
  }
}

/** One OutputChannel per run; re-watching an active run just reveals it. */
export class RunOutputManager implements vscode.Disposable {
  private channels = new Map<string, { ch: vscode.OutputChannel; active: boolean }>();
  watch(client: TdtClient, runId: string, title: string, onLanded?: (run: Run) => void): void {
    const existing = this.channels.get(runId);
    if (existing) { existing.ch.show(true); if (existing.active) return; }
    const ch = existing?.ch ?? vscode.window.createOutputChannel(`TDT run ${runId.slice(0, 8)} — ${title}`);
    const entry = { ch, active: true }; this.channels.set(runId, entry); ch.clear(); ch.show(true);
    let cancelled = false;
    void vscode.window.withProgress({ location: vscode.ProgressLocation.Window, title: `TDT: watching ${title}`, cancellable: true }, async (_p, token) => {
      token.onCancellationRequested(() => (cancelled = true));
      try { const run = await tailRun(client, runId, ch, { isCancelled: () => cancelled }); entry.active = false; if (!cancelled) onLanded?.(run); }
      catch (e) { entry.active = false; ch.appendLine(`✕ ${e instanceof Error ? e.message : String(e)}`); }
    });
  }
  dispose() { for (const { ch } of this.channels.values()) ch.dispose(); this.channels.clear(); }
}
```

```ts
// services/vscode/src/output/planDocument.ts
import * as vscode from "vscode";
import type { TdtClient } from "../api/client";
import { PLAN_SCHEME } from "../ids";

export type LineKind = "add" | "change" | "destroy" | "replace" | "other";
export function planLineKinds(text: string): LineKind[] {
  return text.split("\n").map((l) => {
    const t = l.replace(/^\s+/, "");
    if (t.startsWith("-/+") || t.startsWith("+/-")) return "replace";
    if (t.startsWith("+ ") || t.startsWith("+resource") || /^\+\s*$/.test(t)) return "add";
    if (t.startsWith("~ ")) return "change";
    if (t.startsWith("- ")) return "destroy";
    return "other";
  });
}
export function planUri(runId: string, label: string) { return vscode.Uri.parse(`${PLAN_SCHEME}:${encodeURIComponent(label)}.tfplan.txt?run=${runId}`); }

export class PlanDocumentProvider implements vscode.TextDocumentContentProvider, vscode.Disposable {
  private cache = new Map<string, string>();
  private changed = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this.changed.event;
  private decos: Record<Exclude<LineKind, "other">, vscode.TextEditorDecorationType> = {
    add: vscode.window.createTextEditorDecorationType({ isWholeLine: true, backgroundColor: new vscode.ThemeColor("diffEditor.insertedLineBackground") }),
    destroy: vscode.window.createTextEditorDecorationType({ isWholeLine: true, backgroundColor: new vscode.ThemeColor("diffEditor.removedLineBackground") }),
    change: vscode.window.createTextEditorDecorationType({ isWholeLine: true, backgroundColor: new vscode.ThemeColor("editor.wordHighlightBackground") }),
    replace: vscode.window.createTextEditorDecorationType({ isWholeLine: true, backgroundColor: new vscode.ThemeColor("editorWarning.background"), border: "0 0 0 2px solid", borderColor: new vscode.ThemeColor("editorWarning.foreground") }),
  };
  private subs: vscode.Disposable[] = [];
  constructor(private readonly client: () => TdtClient | undefined) {
    this.subs.push(vscode.workspace.registerTextDocumentContentProvider(PLAN_SCHEME, this),
      vscode.window.onDidChangeActiveTextEditor((ed) => ed && this.decorate(ed)));
  }
  provideTextDocumentContent(uri: vscode.Uri) { return this.cache.get(uri.toString()) ?? "(loading…)"; }
  async open(runId: string, label: string): Promise<void> {
    const c = this.client(); if (!c) throw new Error("Not signed in.");
    const uri = planUri(runId, label);
    const { plan_output } = await c.getPlan(runId);
    this.cache.set(uri.toString(), plan_output?.trim() ? plan_output : "(no plan output recorded for this run yet)");
    this.changed.fire(uri);
    const doc = await vscode.workspace.openTextDocument(uri);
    const ed = await vscode.window.showTextDocument(doc, { preview: true });
    this.decorate(ed);
  }
  private decorate(ed: vscode.TextEditor) {
    if (ed.document.uri.scheme !== PLAN_SCHEME) return;
    const kinds = planLineKinds(ed.document.getText());
    const by: Record<string, vscode.Range[]> = { add: [], change: [], destroy: [], replace: [] };
    kinds.forEach((k, i) => { if (k !== "other") by[k].push(ed.document.lineAt(i).range); });
    for (const k of Object.keys(this.decos) as Array<keyof typeof this.decos>) ed.setDecorations(this.decos[k], by[k]);
  }
  dispose() { for (const d of this.subs) d.dispose(); for (const d of Object.values(this.decos)) d.dispose(); this.changed.dispose(); }
}
```

```ts
// services/vscode/src/commands/run.ts
import * as vscode from "vscode";
import type { Session } from "../session";
import type { Run } from "../api/types";
import type { RunOutputManager } from "../output/runOutput";
import type { PlanDocumentProvider } from "../output/planDocument";
import { RunNode, WorkspaceNode } from "../views/nodes";
import { wrap } from "./auth";

async function pickRun(s: Session, filter?: (r: Run) => boolean): Promise<Run | undefined> {
  const runs = s.store.runs.filter(filter ?? (() => true));
  const pick = await vscode.window.showQuickPick(runs.map((r) => ({ label: `${s.store.workspace(r.workspace_id)?.name ?? r.workspace_id} · ${r.command}`, description: `${r.status} · ${r.id.slice(0, 8)}`, run: r })), { placeHolder: "Run" });
  return pick?.run;
}
const asRun = async (s: Session, arg: unknown, filter?: (r: Run) => boolean) => arg instanceof RunNode ? arg.run : pickRun(s, filter);
const wsName = (s: Session, r: Run) => s.store.workspace(r.workspace_id)?.name ?? r.workspace_id.slice(0, 8);

export function registerRunCommands(ctx: vscode.ExtensionContext, s: Session, out: RunOutputManager, plans: PlanDocumentProvider) {
  const watch = (r: Run) => out.watch(s.requireClient(), r.id, wsName(s, r), (landed) => {
    void s.store.refresh();
    if (landed.status === "awaiting_approval") void vscode.window.showInformationMessage(`TDT: ${wsName(s, landed)} ${landed.command} is awaiting approval.`, "Show plan", "Approve…").then((a) => { if (a === "Show plan") void plans.open(landed.id, wsName(s, landed)); if (a === "Approve…") void vscode.commands.executeCommand("terraducktel.approve", new RunNode(landed)); });
    else if (landed.status === "failed") void vscode.window.showErrorMessage(`TDT: ${wsName(s, landed)} ${landed.command} failed — see the run output.`);
  });
  ctx.subscriptions.push(
    vscode.commands.registerCommand("terraducktel.watchRun", wrap(async (arg) => { const r = await asRun(s, arg); if (r) watch(r); })),
    vscode.commands.registerCommand("terraducktel.showPlan", wrap(async (arg) => { const r = await asRun(s, arg); if (r) await plans.open(r.id, wsName(s, r)); })),
    vscode.commands.registerCommand("terraducktel.approve", wrap(async (arg) => {
      const c = s.requireClient(); const r = await asRun(s, arg, (x) => x.status === "awaiting_approval"); if (!r) return;
      const g = await c.getGraph(r.id).catch(() => undefined); const sm = g?.summary ?? {};
      const msg = `Approve ${r.command} on ${wsName(s, r)}?\n\n+${sm.add ?? 0} to add, ~${sm.change ?? 0} to change, -${sm.destroy ?? 0} to destroy, ±${sm.replace ?? 0} to replace.`;
      const a = await vscode.window.showWarningMessage(msg, { modal: true }, "Approve", "Show plan");
      if (a === "Show plan") { await plans.open(r.id, wsName(s, r)); return; }
      if (a !== "Approve") return;
      await c.approve(r.id); void vscode.window.showInformationMessage(`TDT: approved ${wsName(s, r)} ${r.command}.`); await s.store.refresh(); watch(r);
    })),
    vscode.commands.registerCommand("terraducktel.reject", wrap(async (arg) => {
      const c = s.requireClient(); const r = await asRun(s, arg, (x) => x.status === "awaiting_approval"); if (!r) return;
      const reason = await vscode.window.showInputBox({ prompt: `Reject ${r.command} on ${wsName(s, r)} — reason (optional)` }); if (reason === undefined) return;
      await c.reject(r.id, reason || undefined); await s.store.refresh();
    })),
    vscode.commands.registerCommand("terraducktel.cancelRun", wrap(async (arg) => {
      const c = s.requireClient(); const r = await asRun(s, arg, (x) => ["pending", "running", "planning", "planned", "awaiting_approval"].includes(x.status)); if (!r) return;
      await c.cancel(r.id); await s.store.refresh();
    })),
    vscode.commands.registerCommand("terraducktel.openInBrowser", wrap(async (arg) => {
      const ui = s.uiUrl(); if (!ui) throw new Error("No profile.");
      const url = arg instanceof RunNode ? `${ui}/runs/${arg.run.id}` : arg instanceof WorkspaceNode ? `${ui}/` : `${ui}/runs`;
      await vscode.env.openExternal(vscode.Uri.parse(url));
    })),
    vscode.commands.registerCommand("terraducktel.copyId", wrap(async (arg) => {
      const id = arg instanceof RunNode ? arg.run.id : arg instanceof WorkspaceNode ? arg.ws.id : undefined; if (!id) return;
      await vscode.env.clipboard.writeText(id); void vscode.window.setStatusBarMessage(`Copied ${id}`, 2000);
    })),
  );
  return { watch };
}
```

```ts
// services/vscode/src/commands/workspace.ts
import * as vscode from "vscode";
import type { Session } from "../session";
import type { Workspace } from "../api/types";
import { WorkspaceNode } from "../views/nodes";
import { wrap } from "./auth";
import type { Run } from "../api/types";

async function pickWorkspace(s: Session): Promise<Workspace | undefined> {
  const pick = await vscode.window.showQuickPick(s.store.workspaces.map((w) => ({ label: w.name, description: w.tf_working_dir, detail: `${w.environment} · ${w.repo_ref}`, ws: w })), { placeHolder: "Workspace", matchOnDescription: true });
  return pick?.ws;
}
const asWs = async (s: Session, arg: unknown) => arg instanceof WorkspaceNode ? arg.ws : pickWorkspace(s);

export function registerWorkspaceCommands(ctx: vscode.ExtensionContext, s: Session, watch: (r: Run) => void) {
  const trigger = async (arg: unknown, command: "plan" | "apply" | "destroy") => {
    const c = s.requireClient(); const ws = await asWs(s, arg); if (!ws) return;
    if (command === "apply") { const ok = await vscode.window.showWarningMessage(`Apply ${ws.name}? The plan will pause for approval before anything changes.`, { modal: true }, "Start apply"); if (ok !== "Start apply") return; }
    if (command === "destroy") { const typed = await vscode.window.showInputBox({ prompt: `Type the workspace name to confirm DESTROY: ${ws.name}`, validateInput: (v) => (v === ws.name ? undefined : "Name does not match") }); if (typed !== ws.name) return; }
    const run = await c.triggerRun(ws.id, { command });
    void vscode.window.showInformationMessage(`TDT: ${command} started on ${ws.name} (${run.id.slice(0, 8)}).`);
    await s.store.refresh(); watch(run);
  };
  ctx.subscriptions.push(
    vscode.commands.registerCommand("terraducktel.plan", wrap((a) => trigger(a, "plan"))),
    vscode.commands.registerCommand("terraducktel.apply", wrap((a) => trigger(a, "apply"))),
    vscode.commands.registerCommand("terraducktel.destroy", wrap((a) => trigger(a, "destroy"))),
    vscode.commands.registerCommand("terraducktel.setBranch", wrap(async (arg) => {
      const c = s.requireClient(); const ws = await asWs(s, arg); if (!ws) return;
      const b = await c.listBranches(ws.id).catch(() => ({ source: "none", branches: [] as string[] }));
      let ref: string | undefined;
      if (b.branches.length) { const pick = await vscode.window.showQuickPick([...b.branches.map((x) => ({ label: x })), { label: "$(edit) Other…" }], { placeHolder: `Tracked branch for ${ws.name} (current: ${ws.repo_ref})` }); if (!pick) return; ref = pick.label.startsWith("$(edit)") ? await vscode.window.showInputBox({ prompt: "Branch / ref", value: ws.repo_ref }) : pick.label; }
      else ref = await vscode.window.showInputBox({ prompt: `Tracked branch for ${ws.name}`, value: ws.repo_ref });
      if (!ref || ref === ws.repo_ref) return;
      await c.updateWorkspace(ws.id, { repo_ref: ref }); await s.store.refresh();
    })),
    vscode.commands.registerCommand("terraducktel.syncWorkspace", wrap(async (arg) => { const c = s.requireClient(); const ws = await asWs(s, arg); if (!ws) return; await c.syncWorkspace(ws.id); await s.store.refresh(); })),
  );
}
```

Add to `src/extension.ts` after `registerAuthCommands(context, session);`:

```ts
  const out = new RunOutputManager(); const plans = new PlanDocumentProvider(() => session.client);
  context.subscriptions.push(out, plans);
  const { watch } = registerRunCommands(context, session, out, plans);
  registerWorkspaceCommands(context, session, watch);
```
with the matching imports (`RunOutputManager` from `./output/runOutput`, `PlanDocumentProvider` from `./output/planDocument`, `registerRunCommands` from `./commands/run`, `registerWorkspaceCommands` from `./commands/workspace`).

Implementer note: `test/unit/vscode-stub.ts` must grow whatever these modules touch at import time (`ProgressLocation`, `window.createTextEditorDecorationType`, `workspace.registerTextDocumentContentProvider`, `window.onDidChangeActiveTextEditor`, `Uri.parse` returning `{scheme, toString}`) — add minimal stubs rather than restructuring the modules; the unit tests import only `tailRun` and `planLineKinds`.

- [ ] **Step 4: Verify** — `npm test`, `npm run typecheck`, `npm run build` all green.

- [ ] **Step 5: Commit**

```bash
git add services/vscode/src services/vscode/test
git commit -m "feat(vscode): run tailing, plan document with diff colouring, gated approve/reject

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

### Task 8: Integration smoke, CI job, docs

**Files:**
- Create: `services/vscode/test/integration/runTest.ts`, `test/integration/suite/index.ts`, `test/integration/suite/smoke.test.ts`, `test/integration/stub-server.js`, `docs/VSCODE.md`
- Modify: `.github/workflows/ci-cd.yml` (add `vscode` job), `CLAUDE.md` (repo map row), `docs/ARCHITECTURE.md` (clients section next to the CLI)

- [ ] **Step 1: Integration harness + smoke**

```ts
// services/vscode/test/integration/runTest.ts
import * as path from "node:path";
import { runTests } from "@vscode/test-electron";
import { fork } from "node:child_process";

async function main() {
  const extensionDevelopmentPath = path.resolve(__dirname, "../../");
  const extensionTestsPath = path.resolve(__dirname, "./suite/index");
  // Stub TDT API on a fixed port the smoke test's settings point at.
  const stub = fork(path.resolve(__dirname, "./stub-server.js"), [], { env: { ...process.env, STUB_PORT: "48765" }, stdio: "inherit" });
  await new Promise((r) => setTimeout(r, 500));
  try {
    await runTests({ extensionDevelopmentPath, extensionTestsPath, launchArgs: ["--disable-extensions", "--disable-gpu"], extensionTestsEnv: { TDT_STUB_URL: "http://127.0.0.1:48765" } });
  } finally { stub.kill(); }
}
main().catch((e) => { console.error(e); process.exit(1); });
```

```js
// services/vscode/test/integration/stub-server.js — plain Node, no build step
const http = require("node:http");
const ws = [{ id: "w1", business_unit_id: "b", name: "vpc", environment: "dev", aws_account_id: "123456789012", region: "eu-west-1", repo_url: "local://", tf_working_dir: "account-123456789012/eu-west-1/vpc", repo_ref: "main", kind: "terraform", tags: {}, drift_status: "clean", path_status: "ok", state_backend: "s3" }];
let runs = [{ id: "r1", workspace_id: "w1", command: "plan", status: "planned", created_at: "2026-09-12T10:00:00Z" }];
const steps = [{ id: "s1", run_id: "r2", position: 0, name: "Init", status: "success", output: "ok\n" }, { id: "s2", run_id: "r2", position: 1, name: "Plan", status: "success", output: "No changes.\n" }];
const json = (res, code, body) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(body)); };
http.createServer((req, res) => {
  const u = new URL(req.url, "http://x"); const p = u.pathname; const auth = req.headers.authorization;
  if (p === "/api/v1/auth/config") return json(res, 200, { mode: "local", oidc_enabled: false, cli_loopback: true });
  if (!auth || auth !== "Bearer tdt_smoke") return json(res, 401, { detail: "unauthenticated" });
  if (p === "/api/v1/workspaces") return json(res, 200, ws);
  if (p === "/api/v1/runs") return json(res, 200, runs);
  if (p === "/api/v1/workspaces/w1/runs" && req.method === "POST") { const r = { id: "r2", workspace_id: "w1", command: "plan", status: "planned", created_at: "2026-09-12T11:00:00Z" }; runs = [r, ...runs]; return json(res, 201, r); }
  if (p === "/api/v1/runs/r2") return json(res, 200, runs[0]);
  if (p === "/api/v1/runs/r2/steps") return json(res, 200, steps);
  if (p === "/api/v1/runs/r2/plan") return json(res, 200, { plan_output: "No changes. Your infrastructure matches the configuration." });
  return json(res, 404, { detail: `stub: no route ${req.method} ${p}` });
}).listen(Number(process.env.STUB_PORT || 48765), "127.0.0.1");
```

```ts
// services/vscode/test/integration/suite/index.ts
import * as path from "node:path";
import Mocha from "mocha";
export function run(): Promise<void> {
  const mocha = new Mocha({ ui: "tdd", color: true, timeout: 60_000 });
  mocha.addFile(path.resolve(__dirname, "./smoke.test.js"));
  return new Promise((ok, fail) => mocha.run((failures) => (failures ? fail(new Error(`${failures} test(s) failed`)) : ok())));
}
```

```ts
// services/vscode/test/integration/suite/smoke.test.ts
import * as assert from "node:assert";
import * as vscode from "vscode";

suite("Terraducktel extension smoke", () => {
  test("activates, signs in with an API key against the stub, renders workspaces, triggers a plan", async () => {
    const ext = vscode.extensions.getExtension("terraducktel.terraducktel-vscode")!;
    assert.ok(ext, "extension not found");
    const cfg = vscode.workspace.getConfiguration("terraducktel");
    await cfg.update("profiles", [{ name: "stub", url: process.env.TDT_STUB_URL, bu: "b" }], vscode.ConfigurationTarget.Global);
    await cfg.update("activeProfile", "stub", vscode.ConfigurationTarget.Global);
    await ext.activate();
    // Sign in without UI: store the API key the way the sign-in command would.
    await ext.exports?.__test?.signInWithApiKey?.("tdt_smoke");
    await new Promise((r) => setTimeout(r, 1500));
    const names = await ext.exports.__test.workspaceNames();
    assert.deepStrictEqual(names, ["vpc"]);
    const run = await ext.exports.__test.triggerPlan("w1");
    assert.strictEqual(run.id, "r2");
    await new Promise((r) => setTimeout(r, 1500));
    const runIds = await ext.exports.__test.runIds();
    assert.ok(runIds.includes("r2"));
  });
});
```

Add a **test-only export surface** to `src/extension.ts` (returned from `activate`): `{ __test: { signInWithApiKey: (k) => session.tokens!.signInWithApiKey(k).then(() => session.store.refresh()), workspaceNames: async () => session.store.workspaces.map((w) => w.name), runIds: async () => session.store.runs.map((r) => r.id), triggerPlan: (id) => session.requireClient().triggerRun(id, { command: "plan" }) } }`. It is inert for users (nothing calls it) and keeps the smoke free of UI automation.

Run: `cd services/vscode && npm run test:integration`. This downloads a VS Code build into `.vscode-test/` on first run (network). If the download is impossible in your environment, report it as a concern with the exact error and continue; the harness must still compile (`tsc -p tsconfig.test.json`).

- [ ] **Step 2: CI job** — append to `.github/workflows/ci-cd.yml` after the `ui` job:

```yaml
  vscode:
    name: VS Code extension (type-check, unit tests, package)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: services/vscode/package-lock.json
      - name: Install dependencies
        working-directory: services/vscode
        run: npm ci
      - name: Type-check + unit tests
        working-directory: services/vscode
        run: npm run typecheck && npm test
      - name: Package
        working-directory: services/vscode
        run: npm run package
      - uses: actions/upload-artifact@v4
        with:
          name: terraducktel-vscode
          path: services/vscode/terraducktel-vscode.vsix
```

Validate the YAML parses: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci-cd.yml'))"` (use `services/api/venv/bin/python` if `yaml` is missing on the system python).

- [ ] **Step 3: Docs**

`docs/VSCODE.md`:

```markdown
# Terraducktel for VS Code

Drive TDT from the editor: a Workspaces tree grouped like the web UI, a Runs
tree, plan/apply/destroy with live step output, the plan as a diff-coloured
document, and gated approvals.

## Install

Build the `.vsix` and install it:

```bash
cd services/vscode && npm ci && npm run package
code --install-extension terraducktel-vscode.vsix
```

(CI also uploads `terraducktel-vscode.vsix` as an artifact on every push.)

## Configure a profile

Settings → Terraducktel → Profiles (or `settings.json`):

```json
"terraducktel.profiles": [
  { "name": "local", "url": "http://localhost:8001", "uiUrl": "http://localhost:3001", "bu": "default" },
  { "name": "prod",  "url": "https://tdt.example.com", "bu": "platform" }
],
"terraducktel.activeProfile": "local"
```

`url` is the API origin; `uiUrl` is only needed when the web UI lives on a
different origin (as in the dev compose stack). `insecureTls: true` skips
certificate verification for a self-signed dev stack and shows a warning in
the tree while active.

## Sign in

Command Palette → **Terraducktel: Sign in**. The extension asks the server
which providers exist and offers:

| Mode | Notes |
|---|---|
| SSO | Opens your browser; the server hands the tokens back on `127.0.0.1` (same flow as `tdt login --sso`). Not available in Remote-SSH — use an API key there. |
| Email + password | Stores only the refresh token (VS Code SecretStorage); renews itself. |
| API key | Paste a `tdt_…` key minted in the UI. Bound to one business unit. |

Nothing secret is written to settings or logs.

## Use

- **Workspaces** view: provider → account → region → folders → workspace, each
  with drift and last-run status; expand a workspace for its recent runs.
  Right-click for Plan / Apply… / Destroy… / Set tracked branch / Sync from
  repo / Open in browser / Copy id.
- **Runs** view: newest first, awaiting-approval on top (badge = count).
  Click a run to watch its steps in an output channel; use the inline icons
  for plan output, Approve…, Reject.
- **Approve…** shows `+add ~change -destroy ±replace` from the plan graph and
  needs an explicit click; **Destroy…** asks you to type the workspace name.
- Title-bar buttons: refresh, switch business unit.

Settings: `refreshIntervalSeconds` (default 30), `runsLimit` (200), `trace`
(request metadata to the *Terraducktel* output channel; never credentials).

## Development

`npm run watch` + F5 (Extension Development Host). `npm test` runs the unit
suite against an in-process fake API; `npm run test:integration` runs a
headless VS Code smoke against a stub server. Every endpoint the extension
calls is listed in `api_contract.json` and guarded by
`services/api/tests/test_vscode_api_contract.py`.
```

`CLAUDE.md` repo map: add `| \`services/vscode/\` | VS Code extension: workspaces/runs trees, run triggering + step tailing, plan document, gated approvals. TypeScript, no runtime deps; endpoints pinned by \`api_contract.json\`. |` after the `services/ui/` row.

`docs/ARCHITECTURE.md`: in the section that describes the `tdt` CLI as a client (grep `## ` headings for "CLI" / "clients"; if none, add a short "### Clients" subsection under the service topology), add a paragraph: the VS Code extension is a second first-class client, talks only to `/api/v1`, uses the same three auth modes including the CLI loopback hand-off, and pins its endpoints in `services/vscode/api_contract.json` guarded by `test_vscode_api_contract.py`.

- [ ] **Step 4: Verify** — `npm run typecheck && npm test && npm run package` in `services/vscode`; `./venv/bin/python -m pytest tests/test_vscode_api_contract.py tests/test_api_docs_coverage.py -q` in `services/api`; YAML parse check.

- [ ] **Step 5: Commit**

```bash
git add services/vscode .github/workflows/ci-cd.yml docs/VSCODE.md CLAUDE.md docs/ARCHITECTURE.md
git commit -m "feat(vscode): headless smoke test, CI job, and docs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BkyJ78PBnhmi5cuoB5xHit"
```

---

## Self-review checklist (done while writing)

- **Spec coverage (milestone A):** §1 layout/toolchain → T1; contract → T2; §2 profiles/secrets/modes/refresh/BU → T3+T4+T6; §3 trees/data/actions → T5+T6+T7; §4 trigger/tail/plan doc/approve → T7; §7 contract/TLS/errors/no telemetry → T2, T3 (`insecureTls`), T6 (tree warning), commands `wrap()`; §8 unit + integration + backend → T3–T8; §9 make targets/CI/docs → T1, T8.
- **Type consistency:** `TokenProvider` (getAccessToken/refreshAccessToken/signOut) is identical in T3 tests, T3 client, T4 manager. `Store.runsFor/workspace/run/lastError/consecutiveFailures` match between T5 and T6/T7. `RunNode.run`, `WorkspaceNode.ws` used identically in T6 and T7. `PLAN_LANDED_STATUSES` defined in T3 types and used in T7. `wrap()` defined in T6 auth commands and reused in T7.
- **Spec deviation recorded:** `/drift/summary` is not needed in A (workspaces already carry `drift_status`); it is left out of the contract. `GET /workspaces/{id}` is in the contract for a later tooltip refresh but unused in A — acceptable, or drop it if the reviewer objects.
