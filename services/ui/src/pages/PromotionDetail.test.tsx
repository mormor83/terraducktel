import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
import PromotionDetail from "./PromotionDetail";

const STAGES = (over: Record<string, string> = {}) =>
  ["committed", "checkov", "plan", "opa", "cost", "approval", "apply", "verified"].map((k) => ({
    key: k,
    label: k[0].toUpperCase() + k.slice(1),
    status: (over[k] ?? "pending") as string,
    detail: k === "plan" ? "+1 ~2 −0" : null,
  }));

function promo(over: Record<string, unknown> = {}, stackOver: Record<string, unknown> = {}) {
  return {
    id: "p1", number: 7, link_id: "l1", link_name: "dev-to-prod", kind: "promote",
    reverts_promotion_id: null, direction: "forward", initiated_by: "u1", initiated_by_email: "admin@test.com",
    reason: null, status: "awaiting_approval", error: null, commit_message: "promote(dev-to-prod): a → b\n",
    commits: [{ repo_url: "https://github.com/acme/infra", branch: "main", base_sha: "b".repeat(40),
      sha: "abcdef1234567890", files: ["x/main.tf"], web_url: "https://github.com/acme/infra/commit/abcdef1234567890" }],
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    reverted_by: null,
    stacks: [{
      promotion_run_id: "pr1", pair_id: "pa1", target_stack_id: "ws2", target_stack_name: "stack",
      target_path: "account-2/us-east-1/app/stack", created_stack: false, run_id: "run1",
      run_status: "awaiting_approval", residual: null, verified_at: null,
      stages: STAGES({ committed: "success", checkov: "success", plan: "success", opa: "success",
        cost: "success", approval: "running" }),
      ...stackOver,
    }],
    ...over,
  };
}

function mockGet(data: unknown) {
  vi.mocked(api.get).mockImplementation((url?: string) => {
    if ((url ?? "").startsWith("/v1/promotions/")) return Promise.resolve({ data } as any);
    return Promise.resolve({ data: [] } as any);
  });
}

const renderAt = () =>
  render(
    <MemoryRouter initialEntries={["/governance/environments/l1/promotions/p1"]}>
      <Routes>
        <Route path="/governance/environments/:id/promotions/:pid" element={<PromotionDetail />} />
        <Route path="/runs/:id" element={<p>run page</p>} />
      </Routes>
    </MemoryRouter>,
  );

describe("PromotionDetail", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    currentUser.value = { ...currentUser.value, is_superadmin: true };
  });

  it("renders the stage tracker and links to the run for approval", async () => {
    mockGet(promo());
    renderAt();
    expect(await screen.findByText("Promotion #7")).toBeInTheDocument();
    for (const k of ["committed", "checkov", "plan", "opa", "cost", "approval", "apply", "verified"]) {
      expect(screen.getByTestId(`stage-${k}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("stage-approval")).toHaveAttribute("data-status", "running");
    expect(screen.getByText("+1 ~2 −0")).toBeInTheDocument();
    const approve = screen.getByRole("link", { name: "Approve in Runs →" });
    expect(approve).toHaveAttribute("href", "/runs/run1");
    // No approve button on this page — approval stays in the existing UI.
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    // Commit linked to the repo host.
    expect(screen.getByRole("link", { name: "abcdef1234" })).toHaveAttribute(
      "href", "https://github.com/acme/infra/commit/abcdef1234567890");
    // Not final → no revert.
    expect(screen.queryByRole("button", { name: "Revert…" })).toBeNull();
  });

  it("shows residual differences after verify", async () => {
    mockGet(promo({ status: "succeeded" }, {
      run_status: "applied", verified_at: new Date().toISOString(),
      residual: { in_sync: false, promotable: 2, keys: ["module.app.version", "inputs.x"] },
      stages: STAGES({ committed: "success", apply: "success", verified: "warning" }),
    }));
    renderAt();
    expect(await screen.findByText("2 differences left")).toBeInTheDocument();
    expect(screen.getByText("module.app.version")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to compare →" })).toHaveAttribute("href", "/governance/environments/l1");
  });

  it("shows in sync after verify", async () => {
    mockGet(promo({ status: "succeeded" }, {
      run_status: "applied", verified_at: new Date().toISOString(), residual: { in_sync: true, promotable: 0 },
    }));
    renderAt();
    expect(await screen.findByText(/^In sync/)).toBeInTheDocument();
  });

  it("offers revert only when final and a BU admin, and navigates to the revert", async () => {
    mockGet(promo({ status: "succeeded" }, { run_status: "applied" }));
    vi.mocked(api.post).mockResolvedValue({ data: { ...promo(), id: "p2", number: 8, kind: "revert" } } as any);
    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: "Revert…" }));
    fireEvent.click(screen.getByRole("button", { name: "Revert" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/v1/promotions/p1/revert"));
  });

  it("hides revert for non-BU-admins", async () => {
    currentUser.value = { ...currentUser.value, is_superadmin: false };
    mockGet(promo({ status: "succeeded" }, { run_status: "applied" }));
    renderAt();
    await screen.findByText("Promotion #7");
    expect(screen.queryByRole("button", { name: "Revert…" })).toBeNull();
  });

  it("shows the revert API error inline", async () => {
    mockGet(promo({ status: "failed" }, { run_status: "failed" }));
    vi.mocked(api.post).mockRejectedValue({ response: { data: { detail: "A target stack has a run in progress" } } });
    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: "Revert…" }));
    fireEvent.click(screen.getByRole("button", { name: "Revert" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A target stack has a run in progress");
  });
});
