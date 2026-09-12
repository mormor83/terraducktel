import { describe, expect, it } from "vitest";
import { planLineKinds } from "../../src/output/planDocument";
describe("planLineKinds", () => {
  it("classifies terraform plan lines", () => {
    const text = ["Terraform will perform the following actions:", "  # aws_s3_bucket.b will be created", "  + resource \"aws_s3_bucket\" \"b\" {", "      + bucket = \"x\"", "  ~ update in-place", "  - resource \"x\" \"y\" {", "-/+ resource \"a\" \"b\" (replace)", "Plan: 1 to add, 1 to change, 1 to destroy."].join("\n");
    expect(planLineKinds(text)).toEqual(["other", "other", "add", "add", "change", "destroy", "replace", "other"]);
  });
});
