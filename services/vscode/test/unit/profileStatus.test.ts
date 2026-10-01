import { describe, expect, it } from "vitest";
import * as vscodeStub from "./vscode-stub";
import type { Session } from "../../src/session";
import { ProfileStatus } from "../../src/views/profileStatus";

function session(over: { signedIn?: boolean; bus?: string[]; visible?: string[]; hasProfiles?: boolean } = {}) {
  const storeListeners: Array<() => void> = [];
  const state = { signedIn: true, bus: ["a", "b", "c"], visible: ["a", "b"], hasProfiles: true, ...over };
  const s = {
    get hasProfiles() { return state.hasProfiles; }, profile: { name: "prod" }, tokens: { isSignedIn: () => state.signedIn },
    visibleSlugs: () => state.visible, store: { get bus() { return state.bus.map((slug) => ({ slug })); }, onDidChange: (l: () => void) => { storeListeners.push(l); return { dispose() {} }; } },
    onDidChange: () => ({ dispose() {} }),
  } as unknown as Session;
  return { s, state, fireStore: () => storeListeners.forEach((l) => l()) };
}
const item = () => vscodeStub.statusBarItems.at(-1)!;

describe("ProfileStatus", () => {
  it("shows the profile and how many BUs are visible", () => {
    new ProfileStatus(session().s);
    expect(item().text).toBe("$(server) prod · 2/3 BUs");
  });

  it("always shows X/Y, even when every BU is visible", () => {
    new ProfileStatus(session({ visible: ["a", "b", "c"] }).s);
    expect(item().text).toBe("$(server) prod · 3/3 BUs");
  });

  it("shows only the profile while signed out", () => {
    new ProfileStatus(session({ signedIn: false }).s);
    expect(item().text).toBe("$(server) prod");
  });

  it("updates when the store learns about BUs", () => {
    const f = session({ bus: [], visible: [] }); new ProfileStatus(f.s);
    expect(item().text).toBe("$(server) prod");
    f.state.bus = ["a"]; f.state.visible = ["a"]; f.fireStore();
    expect(item().text).toBe("$(server) prod · 1/1 BUs");
  });
});
