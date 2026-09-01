import { describe, expect, it } from "vitest";

import { matchesTag, tagsAsSearchText } from "./tagMatch";
import type { Workspace } from "./types";

const ws = (tags?: Record<string, string> | null): Workspace =>
  ({
    id: "w",
    name: "w",
    environment: "dev",
    region: "us-east-1",
    aws_account_id: "111111111111",
    drift_status: "unknown",
    tags,
  }) as Workspace;

describe("matchesTag", () => {
  it("no filter matches everything", () => {
    expect(matchesTag(ws({ a: "1" }), null)).toBe(true);
    expect(matchesTag(ws(null), null)).toBe(true);
  });

  it("matches an exact key and value", () => {
    expect(matchesTag(ws({ team: "pay" }), { key: "team", value: "pay" })).toBe(
      true,
    );
    expect(matchesTag(ws({ team: "ops" }), { key: "team", value: "pay" })).toBe(
      false,
    );
  });

  it("a null value is a presence check", () => {
    expect(
      matchesTag(ws({ owner: "jane" }), { key: "owner", value: null }),
    ).toBe(true);
    expect(matchesTag(ws({ team: "pay" }), { key: "owner", value: null })).toBe(
      false,
    );
  });

  it("an untagged workspace matches no filter", () => {
    expect(matchesTag(ws(null), { key: "team", value: null })).toBe(false);
    expect(matchesTag(ws({}), { key: "team", value: "pay" })).toBe(false);
  });

  it("distinguishes an empty value from a missing key", () => {
    // The API allows `key=` with an empty value; that is present, not absent.
    expect(matchesTag(ws({ flag: "" }), { key: "flag", value: null })).toBe(
      true,
    );
    expect(matchesTag(ws({ flag: "" }), { key: "flag", value: "" })).toBe(true);
    expect(matchesTag(ws({ flag: "" }), { key: "flag", value: "x" })).toBe(
      false,
    );
  });

  it("is case sensitive on values, matching the API", () => {
    // The API lowercases keys but preserves value case, so "Prod" !== "prod".
    expect(
      matchesTag(ws({ tier: "Prod" }), { key: "tier", value: "prod" }),
    ).toBe(false);
  });
});

describe("tagsAsSearchText", () => {
  it("renders key=value pairs for the fuzzy search", () => {
    expect(tagsAsSearchText({ team: "pay" })).toBe("team=pay");
  });

  it("is empty for no tags", () => {
    expect(tagsAsSearchText(null)).toBe("");
    expect(tagsAsSearchText({})).toBe("");
  });

  it("lets a value be found without knowing its key", () => {
    expect(tagsAsSearchText({ team: "payments" }).includes("payments")).toBe(
      true,
    );
  });
});
