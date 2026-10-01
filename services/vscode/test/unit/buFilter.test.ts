import { describe, expect, it } from "vitest";
import { getHidden, hiddenKey, setVisible, visibleSlugs, type Memento } from "../../src/state/buFilter";

function fakeMemento(initial: Record<string, unknown> = {}): Memento & { data: Record<string, unknown> } {
  const data = { ...initial };
  return { data, get: <T>(k: string) => data[k] as T | undefined, update: async (k: string, v: unknown) => { if (v === undefined) delete data[k]; else data[k] = v; } };
}

describe("BU filter persistence", () => {
  it("keys the hidden list per profile", () => { expect(hiddenKey("prod")).toBe("buHidden.prod"); });

  it("nothing stored means nothing hidden, so a new BU is visible by default", () => {
    const m = fakeMemento();
    expect(getHidden(m, "prod")).toEqual([]);
    expect(visibleSlugs(["a", "b"], getHidden(m, "prod"))).toEqual(["a", "b"]);
  });

  it("stores the complement of the selection as the hidden list", async () => {
    const m = fakeMemento();
    await setVisible(m, "prod", ["a", "b", "c"], ["b"]);
    expect(m.data["buHidden.prod"]).toEqual(["a", "c"]);
    expect(visibleSlugs(["a", "b", "c", "new"], getHidden(m, "prod"))).toEqual(["b", "new"]);
  });

  it("keeps profiles independent", async () => {
    const m = fakeMemento();
    await setVisible(m, "prod", ["a", "b"], ["a"]);
    expect(getHidden(m, "dev")).toEqual([]);
  });

  it("rejects an empty selection and leaves the previous filter untouched", async () => {
    const m = fakeMemento({ "buHidden.prod": ["a"] });
    await expect(setVisible(m, "prod", ["a", "b"], [])).rejects.toThrow(/at least one/i);
    await expect(setVisible(m, "prod", ["a", "b"], ["zzz"])).rejects.toThrow(/at least one/i);
    expect(m.data["buHidden.prod"]).toEqual(["a"]);
  });

  it("selecting everything clears the stored list", async () => {
    const m = fakeMemento({ "buHidden.prod": ["a"] });
    await setVisible(m, "prod", ["a", "b"], ["a", "b"]);
    expect("buHidden.prod" in m.data).toBe(false);
  });

  it("ignores a malformed stored value", () => {
    expect(getHidden(fakeMemento({ "buHidden.p": "nope" }), "p")).toEqual([]);
    expect(getHidden(fakeMemento({ "buHidden.p": ["a", 3] }), "p")).toEqual(["a"]);
  });
});
