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
import Environments from "./Environments";
import EnvLinkBuilder from "./EnvLinkBuilder";

const DEV = "account-333333333333";
const PROD = "account-444444444444";

const LINK = {
  id: "l1",
  business_unit_id: "bu",
  name: "dev-to-prod",
  engine: "terraform",
  source_node: { level: "account", path: DEV },
  target_node: { level: "account", path: PROD },
  level: "account",
  source_account_id: "333333333333",
  target_account_id: "444444444444",
  rewrite_rules: [],
  pair_overrides: { pairs: [], exclude: [] },
  protected_rules: { keys: [], values: [] },
  rules_version: 1,
  created_by: null,
  updated_by: null,
  created_at: "2026-09-28T00:00:00Z",
  updated_at: "2026-09-28T00:00:00Z",
  pair_summary: {
    not_compared: 28, in_sync: 0, diverged: 0, missing_in_target: 59,
    missing_in_source: 0, excluded: 0, total: 87,
  },
  helm_skipped: 0,
  warnings: [],
  last_promotion: null,
};

const ws = (path: string, kind = "terraform") => ({
  id: path, name: path.split("/").pop(), environment: "dev", region: "us-east-1",
  aws_account_id: path.slice(8, 20), drift_status: "clean", tf_working_dir: path, repo_ref: "main", kind,
});

function mockGet(links: unknown[]) {
  vi.mocked(api.get).mockImplementation((url?: string) => {
    if ((url ?? "").startsWith("/v1/env-links")) return Promise.resolve({ data: links } as any);
    if ((url ?? "") === "/v1/workspaces") {
      return Promise.resolve({
        data: [
          ws(`${DEV}/us-east-1/monitoring/stack`),
          ws(`${PROD}/us-east-1/monitoring/stack`),
          ws(`${DEV}/us-east-1/charts/grafana`, "helm"),
        ],
      } as any);
    }
    return Promise.resolve({ data: [] } as any);
  });
}

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/governance/environments" element={<Environments />} />
        <Route path="/governance/environments/new" element={<EnvLinkBuilder />} />
        <Route path="/governance/environments/:id" element={<p>detail page</p>} />
      </Routes>
    </MemoryRouter>,
  );

describe("Environments list", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    currentUser.value = { ...currentUser.value, is_superadmin: true };
  });

  it("renders one row per link with its pair summary", async () => {
    mockGet([LINK]);
    renderAt("/governance/environments");
    expect(await screen.findByText("dev-to-prod")).toBeInTheDocument();
    expect(screen.getByText("28 not compared · 59 missing in target")).toBeInTheDocument();
  });

  it("shows the empty state with a call to action", async () => {
    mockGet([]);
    renderAt("/governance/environments");
    expect(await screen.findByText("No linked environments yet")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Link environments" })[0]).toBeEnabled();
  });

  it("disables linking for non-BU-admins", async () => {
    currentUser.value = { ...currentUser.value, is_superadmin: false };
    mockGet([]);
    renderAt("/governance/environments");
    await screen.findByText("No linked environments yet");
    for (const b of screen.getAllByRole("button", { name: "Link environments" })) expect(b).toBeDisabled();
  });
});

describe("Link builder", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    currentUser.value = { ...currentUser.value, is_superadmin: true };
  });

  it("previews pairing, seeds protected rules, and creates the link", async () => {
    mockGet([]);
    vi.mocked(api.post).mockImplementation((url?: string) => {
      if (url === "/v1/env-links/preview-pairs") {
        return Promise.resolve({
          data: {
            level: "account", source_account_id: "333333333333", target_account_id: "444444444444",
            pairs: [{
              id: null, relative_path: "us-east-1/monitoring/stack", source_rel: "us-east-1/monitoring/stack",
              target_rel: "us-east-1/monitoring/stack", source_stack_id: "a", target_stack_id: "b",
              source_stack_name: "stack", target_stack_name: "stack", status: "not_compared", reason: null,
              proposed_target_rel: null, summary: null, last_compared_at: null,
            }],
            summary: { ...LINK.pair_summary, not_compared: 1, missing_in_target: 0, total: 1 },
            helm_skipped: 1, warnings: [],
            default_protected_rules: { keys: ["instance_type"], values: ["333333333333"] },
          },
        } as any);
      }
      if (url === "/v1/env-links") return Promise.resolve({ data: { ...LINK, id: "new1" } } as any);
      return Promise.reject(new Error("unexpected " + url));
    });

    renderAt("/governance/environments/new");
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "dev-to-prod" } });

    // Both trees start collapsed to their accounts: [Dev, Prod] on each side.
    const [srcTree, tgtTree] = await screen.findAllByRole("tree");
    fireEvent.click(srcTree.querySelectorAll('input[type="radio"]')[0]);
    fireEvent.click(tgtTree.querySelectorAll('input[type="radio"]')[1]);

    expect(await screen.findByText("1 Helm stack skipped — Helm environments are coming soon.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Keys")).toHaveValue("instance_type"));

    fireEvent.click(screen.getByRole("button", { name: "Create link" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/v1/env-links", expect.objectContaining({
      name: "dev-to-prod",
      source_node: { level: "account", path: DEV },
      target_node: { level: "account", path: PROD },
      protected_rules: { keys: ["instance_type"], values: ["333333333333"] },
    })));
    expect(await screen.findByText("detail page")).toBeInTheDocument();
  });

  it("disables helm-only nodes and nodes at a different level than the other side", async () => {
    mockGet([]);
    vi.mocked(api.post).mockReturnValue(new Promise(() => {}) as any);
    renderAt("/governance/environments/new");
    const trees = await screen.findAllByRole("tree");
    // Expand everything in the source tree via filtering.
    fireEvent.change(screen.getByLabelText("Filter Source"), { target: { value: "grafana" } });
    const helmRadio = await waitFor(() => {
      const r = trees[0].querySelectorAll('input[type="radio"]');
      expect(r.length).toBeGreaterThan(1);
      return r[r.length - 1] as HTMLInputElement;
    });
    expect(helmRadio).toBeDisabled();

    // Picking an account on the target side disables non-account nodes on the source side.
    const tgtRadios = trees[1].querySelectorAll('input[type="radio"]');
    fireEvent.click(tgtRadios[1]); // PROD account
    fireEvent.change(screen.getByLabelText("Filter Source"), { target: { value: "monitoring" } });
    await waitFor(() => {
      const r = [...trees[0].querySelectorAll('input[type="radio"]')] as HTMLInputElement[];
      expect(r[0]).toBeEnabled(); // DEV account
      expect(r.slice(1).every((x) => x.disabled)).toBe(true);
    });
  });
});
