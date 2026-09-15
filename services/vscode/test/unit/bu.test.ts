import { describe, expect, it } from "vitest";
import { migrateLegacyBu, type BuStore } from "../../src/auth/bu";
import type { Profile } from "../../src/auth/profiles";

/** Minimal in-memory fake of the `vscode.Memento` subset `migrateLegacyBu` needs. */
function fakeStore(initial: Record<string, unknown> = {}): BuStore & { data: Record<string, unknown> } {
  const data = { ...initial };
  return {
    data,
    get: <T>(key: string) => data[key] as T | undefined,
    update: async (key: string, value: unknown) => { data[key] = value; },
  };
}

const p = (over: Partial<Profile> & { name: string }): Profile => ({ url: "https://x", ...over });

describe("migrateLegacyBu", () => {
  it("copies a legacy profile's bu into the store under bu.<name>", async () => {
    const store = fakeStore();
    await migrateLegacyBu([p({ name: "prod", bu: "platform" })], store);
    expect(store.data["bu.prod"]).toBe("platform");
  });

  it("skips profiles with no bu", async () => {
    const store = fakeStore();
    await migrateLegacyBu([p({ name: "prod" })], store);
    expect(store.data["bu.prod"]).toBeUndefined();
  });

  it("never overwrites an already-set value (an explicit in-app BU choice wins)", async () => {
    const store = fakeStore({ "bu.prod": "already-chosen" });
    await migrateLegacyBu([p({ name: "prod", bu: "legacy-default" })], store);
    expect(store.data["bu.prod"]).toBe("already-chosen");
  });

  it("migrates every profile that carries a bu, independently", async () => {
    const store = fakeStore();
    await migrateLegacyBu([p({ name: "a", bu: "team-a" }), p({ name: "b" }), p({ name: "c", bu: "team-c" })], store);
    expect(store.data).toEqual({ "bu.a": "team-a", "bu.c": "team-c" });
  });
});
