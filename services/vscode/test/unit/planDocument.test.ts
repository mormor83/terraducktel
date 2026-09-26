import { describe, expect, it } from "vitest";
import type * as vscode from "vscode";
import { planDecorationOptions, planLineKinds } from "../../src/output/planDocument";
describe("planLineKinds", () => {
  it("classifies terraform plan lines", () => {
    const text = ["Terraform will perform the following actions:", "  # aws_s3_bucket.b will be created", "  + resource \"aws_s3_bucket\" \"b\" {", "      + bucket = \"x\"", "  ~ update in-place", "  - resource \"x\" \"y\" {", "-/+ resource \"a\" \"b\" (replace)", "Plan: 1 to add, 1 to change, 1 to destroy."].join("\n");
    expect(planLineKinds(text)).toEqual(["other", "other", "add", "add", "change", "destroy", "replace", "other"]);
  });
});

describe("planDecorationOptions", () => {
  const colour = (c: unknown) => (c as vscode.ThemeColor).id;
  it.each(["add", "change", "destroy", "replace"] as const)("paints %s lines with the terraducktel.* brand colours", (kind) => {
    const o = planDecorationOptions(kind);
    expect(o.isWholeLine).toBe(true);
    expect(colour(o.backgroundColor)).toBe(`terraducktel.${kind}Background`);
    expect(colour(o.color)).toBe(`terraducktel.${kind}`);
    expect(colour(o.overviewRulerColor)).toBe(`terraducktel.${kind}`);
  });
  it("adds a 2px left border in the replace colour to replace lines only", () => {
    expect(planDecorationOptions("replace")).toMatchObject({ border: "0 0 0 2px solid" });
    expect(colour(planDecorationOptions("replace").borderColor)).toBe("terraducktel.replace");
    expect(planDecorationOptions("add").border).toBeUndefined();
  });
});
