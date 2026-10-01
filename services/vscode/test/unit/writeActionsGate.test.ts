import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

type MenuEntry = { command: string; when?: string };
const pkg = JSON.parse(readFileSync(join(__dirname, "../../package.json"), "utf8"));
const entries: MenuEntry[] = Object.values(pkg.contributes.menus as Record<string, MenuEntry[]>).flat();
const WRITE = ["plan", "apply", "destroy", "setBranch", "syncWorkspace", "approve", "reject", "cancelRun", "planCurrentFile"].map((c) => `terraducktel.${c}`);

// Roles are per business unit; the token's global legacy role says nothing about them, so the
// client must not hide write actions on it — the API is the source of truth (403 + message).
describe("write actions are gated on sign-in, not on the token's global role", () => {
  it("no menu `when` clause references terraducktel.canWrite", () => {
    expect(entries.filter((e) => e.when?.includes("terraducktel.canWrite"))).toEqual([]);
  });
  it("every write-action menu entry requires terraducktel.signedIn", () => {
    const writes = entries.filter((e) => WRITE.includes(e.command));
    expect(writes.length).toBeGreaterThan(0);
    for (const e of writes) expect(e.when, e.command).toMatch(/terraducktel\.signedIn/);
  });
});
