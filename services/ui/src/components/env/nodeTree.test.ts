import { describe, expect, it } from "vitest";

import type { Workspace } from "../workspace-tree/types";
import { accountIdOf, buildPathTree, filterTree, findNode } from "./nodeTree";

const ws = (path: string, kind = "terraform", repo_ref = "main"): Workspace => ({
  id: path,
  name: path.split("/").pop() as string,
  environment: "dev",
  region: "us-east-1",
  aws_account_id: "333333333333",
  drift_status: "clean",
  tf_working_dir: path,
  repo_ref,
  kind,
});

const DEV = "account-333333333333";

describe("buildPathTree", () => {
  const tree = buildPathTree([
    ws(`${DEV}/us-east-1/monitoring/stack`, "terraform", "feat/jsm-ops-secrets"),
    ws(`${DEV}/us-east-1/vpc/home`),
    ws(`${DEV}/us-east-1/charts/grafana`, "helm"),
  ]);

  it("assigns account / region / folder / stack levels", () => {
    expect(tree[0].level).toBe("account");
    expect(findNode(tree, `${DEV}/us-east-1`)?.level).toBe("region");
    expect(findNode(tree, `${DEV}/us-east-1/monitoring`)?.level).toBe("folder");
    expect(findNode(tree, `${DEV}/us-east-1/monitoring/stack`)?.level).toBe("stack");
  });

  it("counts terraform and helm stacks separately", () => {
    expect(tree[0].terraformCount).toBe(2);
    expect(tree[0].helmCount).toBe(1);
    expect(findNode(tree, `${DEV}/us-east-1/charts`)?.terraformCount).toBe(0);
  });

  it("filters to matching subtrees, including by pinned branch", () => {
    const f = filterTree(tree, "jsm-ops");
    const region = f[0].children[0];
    expect(region.children.map((c) => c.name)).toEqual(["monitoring"]);
  });

  it("reads the account id from a node path", () => {
    expect(accountIdOf(`${DEV}/us-east-1`)).toBe("333333333333");
    expect(accountIdOf("global/cloudflare")).toBeNull();
  });
});
