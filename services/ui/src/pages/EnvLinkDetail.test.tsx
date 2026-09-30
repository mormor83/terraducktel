import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("../hooks/useBusinessUnit", () => ({
  BU_CHANGED_EVENT: "terraducktel:bu-changed",
  useBusinessUnitSelection: () => ["default", () => {}],
}));

const currentUser = { value: { id: "u1", email: "a@x", role: "admin", is_superadmin: true, name: "A" } };
vi.mock("../hooks/useAuth", () => ({
  useCurrentUser: () => currentUser.value,
  hasMinRole: () => true,
}));

import { api } from "../api/client";
import EnvLinkDetail from "./EnvLinkDetail";

const DEV = "account-333333333333";
const PROD = "account-444444444444";

const LINK = {
  id: "l1", business_unit_id: "bu", name: "dev-to-prod", engine: "terraform",
  source_node: { level: "account", path: DEV }, target_node: { level: "account", path: PROD },
  level: "account", source_account_id: "333333333333", target_account_id: "444444444444",
  rewrite_rules: [], pair_overrides: { pairs: [], exclude: [] }, protected_rules: { keys: [], values: [] },
  rules_version: 1, created_by: null, updated_by: null,
  created_at: "2026-09-28T00:00:00Z", updated_at: "2026-09-28T00:00:00Z",
  pair_summary: { not_compared: 0, in_sync: 0, diverged: 1, missing_in_target: 1, missing_in_source: 0, excluded: 0, total: 2 },
  helm_skipped: 0, warnings: [], last_promotion: null,
};

const pair = (over: Record<string, unknown>) => ({
  id: "p1", relative_path: "us-east-1/monitoring/stack", source_rel: "us-east-1/monitoring/stack",
  target_rel: "us-east-1/monitoring/stack", source_stack_id: "s1", target_stack_id: "t1",
  source_stack_name: "stack", target_stack_name: "stack", status: "diverged", reason: null,
  proposed_target_rel: null, last_compared_at: null,
  summary: { keys_changed: 3, protected_count: 1, module_versions: [{ key: "module.mon", from: "v1.4.0", to: "v1.6.2" }] },
  ...over,
});

const PAIRS = [
  pair({}),
  pair({ id: "p2", relative_path: "us-east-1/vpc/home", status: "missing_in_target", target_rel: null, target_stack_id: null, summary: null, proposed_target_rel: "us-east-1/vpc/home" }),
];

const hunk = (over: Record<string, unknown>) => ({
  id: "h1", file: "main.tf", key: "module.mon.source", category: "module", kind: "changed",
  source_value: '"x?ref=v1.6.2"', target_value: '"x?ref=v1.4.0"', classification: "promotable",
  protected_reason: null, applicable: true, not_applicable_reason: null,
  source_lines: [3, 3], target_lines: [3, 3], level: "key", group: null, ...over,
});

const ref = (branch: string) => ({
  stack_id: "s", stack_name: "stack", repo_url: "https://x/y", branch, commit: "abcdef1234567", path: "p",
  account_id: "1", drift_status: "clean", state_serial: 4, state_resources: 2, state_error: null, error: null,
});

const COMPARE = {
  pair_id: "p1", direction: "forward", status: "ready", stale: false, computed_at: "2026-09-28T00:00:00Z",
  refs: { source: ref("main"), target: ref("main") },
  config_diff: {
    files: [], warnings: [],
    summary: { files_changed: 1, keys_changed: 3, promotable_count: 1, protected_count: 1, backend_count: 1, module_versions: [], providers: [] },
    hunks: [
      hunk({}),
      hunk({ id: "h2", key: "module.mon.instance_type", category: "inputs", classification: "protected", protected_reason: "key matches 'instance_type'", source_value: '"t3.large"', target_value: '"t3.small"' }),
      hunk({ id: "h3", key: "terraform.backend.s3.bucket", category: "other", classification: "backend", applicable: false, not_applicable_reason: "Backend / remote-state config is never promoted" }),
    ],
  },
  state_diff: null, error: null,
  pair: { status: "diverged", summary: null, relative_path: "us-east-1/monitoring/stack" },
};

const PREVIEW = {
  direction: "forward",
  changes: [{
    pair_id: "p1", relative_path: "us-east-1/monitoring/stack", target_stack_id: "t1", target_stack_name: "stack",
    target_path: `${PROD}/us-east-1/monitoring/stack`, repo_url: "https://x/y", branch: "main", base_commit: "abc",
    create: false, hunks: [{ id: "h1", key: "module.mon.source" }],
    files: [{ path: `${PROD}/us-east-1/monitoring/stack/main.tf`, status: "modified", unified: "-a\n+b\n" }],
  }],
  affected_stacks: 1, commits: [{ repo_url: "https://x/y", branch: "main", stacks: 1 }],
  blockers: [] as string[], warnings: ["the target has unresolved drift"], changed_hunks: [], stale: false,
  commit_message: "promote(dev-to-prod): a → b [promotion #<n>]\n", selection_hash: "h",
};

function mockApi(preview = PREVIEW) {
  vi.mocked(api.get).mockImplementation((url?: string) => {
    if (url === "/v1/env-links/l1") return Promise.resolve({ data: LINK } as any);
    if (url === "/v1/env-links/l1/pairs") return Promise.resolve({ data: { items: PAIRS, summary: LINK.pair_summary, warnings: [] } } as any);
    if (url === "/v1/env-pairs/p1/compare") return Promise.resolve({ status: 200, data: COMPARE } as any);
    return Promise.resolve({ data: [] } as any);
  });
  vi.mocked(api.post).mockImplementation((url?: string) => {
    if (url === "/v1/env-links/l1/compare") return Promise.resolve({ data: { queued: 0 } } as any);
    if (url === "/v1/env-links/l1/promotions/preview") return Promise.resolve({ data: preview } as any);
    if (url === "/v1/env-links/l1/promotions") return Promise.resolve({ status: 201, data: { id: "pr1", number: 1 } } as any);
    return Promise.reject(new Error("unexpected " + url));
  });
}

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={["/governance/environments/l1"]}>
      <Routes>
        <Route path="/governance/environments/:id" element={<EnvLinkDetail />} />
        <Route path="/governance/environments/:id/promotions/:pid" element={<p>promotion page</p>} />
      </Routes>
    </MemoryRouter>,
  );

describe("Compare screen", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    currentUser.value = { ...currentUser.value, is_superadmin: true };
  });

  it("lists pairs with their mini summary and shows the semantic table for the selected pair", async () => {
    mockApi();
    renderPage();
    expect(await screen.findByText("3 keys · module v1.4.0 → v1.6.2 · 1 protected")).toBeInTheDocument();
    expect(screen.getByText("us-east-1/vpc/home")).toBeInTheDocument();
    expect(await screen.findByText("module.mon.source")).toBeInTheDocument();
    expect(screen.getByText("Module & versions")).toBeInTheDocument();
    expect(screen.getByLabelText("Select terraform.backend.s3.bucket")).toBeDisabled();
  });

  it("requires a reason before selecting a protected hunk, and counts the selection", async () => {
    mockApi();
    renderPage();
    const prot = await screen.findByLabelText("Select module.mon.instance_type");
    fireEvent.click(prot);
    const dialog = await screen.findByRole("dialog", { name: "Include protected value" });
    const include = within(dialog).getByRole("button", { name: "Include" });
    expect(include).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Reason"), { target: { value: "prod needs it" } });
    fireEvent.click(include);
    await waitFor(() => expect(screen.getByLabelText("Select module.mon.instance_type")).toBeChecked());
    fireEvent.click(screen.getByLabelText("Select module.mon.source"));
    expect(screen.getByText((_, el) => el?.textContent === "2 changes in 1 stack selected")).toBeInTheDocument();
  });

  it("bulk-selects a missing pair as create-in-target", async () => {
    mockApi();
    renderPage();
    fireEvent.click(await screen.findByLabelText("Select us-east-1/vpc/home"));
    expect(screen.getByText((_, el) => el?.textContent === "1 change in 1 stack selected")).toBeInTheDocument();
  });

  it("opens the preview drawer; blockers disable confirm", async () => {
    mockApi({ ...PREVIEW, blockers: ["Git write access is disabled for this Business Unit"] });
    renderPage();
    fireEvent.click(await screen.findByLabelText("Select module.mon.source"));
    fireEvent.click(screen.getByRole("button", { name: "Promote →" }));
    expect(await screen.findByText("Git write access is disabled for this Business Unit")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm & commit" })).toBeDisabled();
    expect(api.post).toHaveBeenCalledWith("/v1/env-links/l1/promotions/preview", expect.objectContaining({
      direction: "forward",
      pairs: [{ pair_id: "p1", hunk_ids: ["h1"], create_in_target: false }],
    }));
  });

  it("confirm commits the promotion and navigates to its tracker", async () => {
    mockApi();
    renderPage();
    fireEvent.click(await screen.findByLabelText("Select module.mon.source"));
    fireEvent.click(screen.getByRole("button", { name: "Promote →" }));
    const confirm = await screen.findByRole("button", { name: "Confirm & commit" });
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/v1/env-links/l1/promotions", expect.objectContaining({
      pairs: [{ pair_id: "p1", hunk_ids: ["h1"], create_in_target: false }],
      commit_message: PREVIEW.commit_message,
    }), expect.anything()));
    expect(await screen.findByText("promotion page")).toBeInTheDocument();
  });

  it("disables promote for non-BU-admins", async () => {
    currentUser.value = { ...currentUser.value, is_superadmin: false };
    mockApi();
    renderPage();
    await screen.findByText("module.mon.source");
    expect(screen.getByRole("button", { name: "Promote →" })).toBeDisabled();
    expect(screen.getByLabelText("Select module.mon.source")).toBeDisabled();
  });
});
