import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

import {
  asAccountColor,
  accountRailClass,
  ACCOUNT_COLORS,
  ACCOUNT_COLOR_CLASSES,
} from "../components/accountColors";

const get = vi.fn();
vi.mock("../api/client", () => ({ api: { get: (...a: unknown[]) => get(...a) } }));

import { useAccountColors } from "./useAccountColors";

const AWS = [
  { id: "pk-1", account_id: "333333333333", name: "Prod-Account", color_effective: "red" },
];
const K8S = [{ id: "cl-1", name: "prod-eks", color_effective: "yellow" }];

beforeEach(() => {
  get.mockReset();
  get.mockImplementation((url: string) => {
    if (url.includes("aws-accounts")) return Promise.resolve({ data: AWS });
    if (url.includes("clusters")) return Promise.resolve({ data: K8S });
    return Promise.resolve({ data: [] });
  });
});

describe("palette helpers", () => {
  it("every token has classes", () => {
    for (const c of ACCOUNT_COLORS) {
      expect(ACCOUNT_COLOR_CLASSES[c].solid).toBeTruthy();
      expect(ACCOUNT_COLOR_CLASSES[c].swatch).toBeTruthy();
      expect(ACCOUNT_COLOR_CLASSES[c].chip).toBeTruthy();
    }
  });

  it("falls back to gray rather than crashing on an unknown/absent colour", () => {
    // Guards against an API older than migration 041 omitting the field — an
    // undefined class lookup would otherwise throw while rendering every row.
    expect(asAccountColor(undefined)).toBe("gray");
    expect(asAccountColor(null)).toBe("gray");
    expect(asAccountColor("chartreuse")).toBe("gray");
    expect(accountRailClass("chartreuse")).toContain("slate");
  });

  it("passes through a valid token", () => {
    expect(asAccountColor("purple")).toBe("purple");
    expect(accountRailClass("purple")).toContain("violet");
  });
});

describe("useAccountColors badgeFor", () => {
  async function badges() {
    const { result } = renderHook(() => useAccountColors());
    await waitFor(() => expect(result.current.loading).toBe(false));
    return result.current.badgeFor;
  }

  it("keys AWS by the 12-digit account id, not the row PK", async () => {
    const badgeFor = await badges();
    const b = badgeFor({ aws_account_id: "333333333333" });
    expect(b).toMatchObject({ color: "red", name: "Prod-Account", id: "333333333333" });
    // The PK must NOT resolve — workspaces store the 12-digit id.
    expect(badgeFor({ aws_account_id: "pk-1" })).toBeNull();
  });

  it("attributes helm workspaces to their cluster", async () => {
    const badgeFor = await badges();
    const b = badgeFor({ kind: "helm", cluster_id: "cl-1", aws_account_id: "333333333333" });
    // Cluster wins over the AWS account for helm — matching the Slack resolver.
    expect(b).toMatchObject({ color: "yellow", name: "prod-eks" });
  });

  it("treats the 'global' sentinel and unknown accounts as no account", async () => {
    const badgeFor = await badges();
    expect(badgeFor({ aws_account_id: "global" })).toBeNull();
    expect(badgeFor({ aws_account_id: "999999999999" })).toBeNull();
    expect(badgeFor(undefined)).toBeNull();
  });

  it("survives a provider endpoint failing", async () => {
    get.mockImplementation((url: string) => {
      if (url.includes("aws-accounts")) return Promise.resolve({ data: AWS });
      return Promise.reject(new Error("403"));
    });
    const badgeFor = await badges();
    // AWS colours still resolve even though the other three 403'd.
    expect(badgeFor({ aws_account_id: "333333333333" })?.color).toBe("red");
    expect(badgeFor({ kind: "helm", cluster_id: "cl-1" })).toBeNull();
  });
});
