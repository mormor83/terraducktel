import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

// Avoid loading Monaco (needs real DOM/workers) — swap the rego editor for a textarea.
vi.mock("../RegoEditor", () => ({
  RegoEditor: ({ value, onChange }: any) => (
    <textarea aria-label="rego" value={value} onChange={(e) => onChange?.(e.target.value)} />
  ),
  RegoDiff: () => <div data-testid="rego-diff" />,
}));

vi.mock("../../hooks/useBusinessUnit", () => ({
  useBusinessUnitSelection: () => ["default", () => {}],
  useBusinessUnits: () => ({ bus: [{ slug: "default", name: "Default" }], loading: false, error: null, refresh: () => {} }),
}));

import PoliciesSection from "./PoliciesSection";
import { api } from "../../api/client";

const OPA_CONFIG = {
  mode: "warn",
  use_bundled: true,
  bundled_severity: "block",
  git_severity: "block",
  repo_url: "",
  repo_ref: "main",
  repo_dir: "",
  inherited: false,
};

const POLICIES = [
  { id: "p1", name: "no-public-buckets", description: "no public S3", rego: "package main",
    tests_rego: null, severity: "block", enabled: true, current_version: 2, updated_at: "2026-06-15T00:00:00Z" },
];

function mockGet(policies = POLICIES) {
  vi.mocked(api.get).mockImplementation((url?: string) => {
    if ((url ?? "").includes("/integrations/opa")) return Promise.resolve({ data: OPA_CONFIG } as any);
    if ((url ?? "").includes("/policies")) return Promise.resolve({ data: policies } as any);
    if ((url ?? "").includes("/runs")) return Promise.resolve({ data: [] } as any);
    return Promise.resolve({ data: {} } as any);
  });
}

describe("PoliciesSection", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.delete).mockReset();
  });

  it("loads the gate config and the policy list", async () => {
    mockGet();
    render(<PoliciesSection />);
    expect(await screen.findByText("no-public-buckets")).toBeInTheDocument();
    // severity badge ("block" also appears in gate-config selects) + version
    expect(screen.getAllByText("block").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("v2")).toBeInTheDocument();
    // gate config card rendered
    expect(screen.getByText("OPA policy gate")).toBeInTheDocument();
  });

  it("creates a new policy", async () => {
    mockGet([]);
    vi.mocked(api.post).mockResolvedValue({ data: {} } as any);
    render(<PoliciesSection />);

    fireEvent.click(await screen.findByText("New policy"));
    fireEvent.change(screen.getByPlaceholderText("no-public-buckets"), { target: { value: "require-tags" } });
    fireEvent.click(screen.getByText("Create policy"));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        "/v1/policies",
        expect.objectContaining({ name: "require-tags", severity: "block" }),
      );
    });
  });

  it("saves the gate config", async () => {
    mockGet();
    vi.mocked(api.put).mockResolvedValue({ data: {} } as any);
    render(<PoliciesSection />);

    fireEvent.click(await screen.findByText("Save gate config"));
    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        "/v1/integrations/opa",
        expect.objectContaining({ mode: "warn", use_bundled: true }),
      );
    });
  });
});
