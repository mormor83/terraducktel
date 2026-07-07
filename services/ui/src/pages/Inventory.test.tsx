import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: { get: vi.fn() },
  getToken: () => null,   // useCurrentUser → non-admin in tests (no ignore-rules UI)
  setToken: () => {},
}));

import Inventory from "./Inventory";
import { api } from "../api/client";

const SUMMARY = {
  total: 4,
  codification_pct: 75,
  counts: { codified: 1, drifted: 1, ghost: 1, unmanaged: 1, ignored: 0, undetermined: 0 },
  facets: { providers: ["aws"], regions: ["us-east-1"], accounts: ["123456789012"], asset_types: ["aws_instance"] },
};

const ASSETS = {
  total: 4,
  items: [
    { asset_id: "arn:aws:ec2:::i-web", address: "aws_instance.web", asset_type: "aws_instance",
      provider: "aws", region: "us-east-1", account_id: "123456789012", iac_status: "codified",
      drift_summary: "", workspace_id: "w1", last_seen: null },
    { asset_id: "arn:aws:s3:::rogue", address: "", asset_type: "s3", provider: "aws",
      region: "us-east-1", account_id: "123456789012", iac_status: "unmanaged",
      drift_summary: "live resource not in tfstate", workspace_id: null, last_seen: null },
  ],
};

// api.get is called for both /summary and /assets; route by URL.
function mockApi(summary = SUMMARY, assets = ASSETS) {
  vi.mocked(api.get).mockImplementation((url?: string) => {
    if ((url ?? "").includes("/summary")) return Promise.resolve({ data: summary } as any);
    return Promise.resolve({ data: assets } as any);
  });
}

describe("Inventory page", () => {
  beforeEach(() => vi.mocked(api.get).mockReset());

  it("renders codification KPI, state cards and the asset table", async () => {
    mockApi();
    render(<Inventory />);

    expect(await screen.findByText("75%")).toBeInTheDocument();
    expect(screen.getByText("Codification")).toBeInTheDocument();
    // "Codified"/"Unmanaged" appear in both a KPI card and a table badge
    expect(screen.getAllByText("Codified").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Unmanaged").length).toBeGreaterThanOrEqual(1);
    // asset rows
    expect(await screen.findByText("aws_instance.web")).toBeInTheDocument();
    expect(screen.getByText("arn:aws:s3:::rogue")).toBeInTheDocument();
  });

  it("filtering by status re-queries the assets endpoint with iac_status", async () => {
    mockApi();
    render(<Inventory />);
    await screen.findByText("aws_instance.web");

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "unmanaged" } });

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(
        "/v1/inventory/assets",
        { params: { iac_status: "unmanaged" } },
      ),
    );
  });

  it("shows an empty state when there are no assets", async () => {
    mockApi(SUMMARY, { total: 0, items: [] });
    render(<Inventory />);
    expect(await screen.findByText("No assets discovered yet")).toBeInTheDocument();
  });
});
