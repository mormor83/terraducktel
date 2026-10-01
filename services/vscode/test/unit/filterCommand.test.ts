import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as vscodeStub from "./vscode-stub";
import type { Session } from "../../src/session";
import { registerAuthCommands } from "../../src/commands/auth";

type Pick = { label: string; description?: string; picked?: boolean; slug: string };
const BUS = [{ id: "1", slug: "alpha", name: "Alpha" }, { id: "2", slug: "beta", name: "Beta" }, { id: "3", slug: "gamma", name: "Gamma" }];

describe("terraducktel.filterBusinessUnits", () => {
  let handlers: Map<string, () => Promise<void>>; let run: () => Promise<void>; let applied: string[][]; let quickPick: ReturnType<typeof vi.spyOn>; let errors: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    handlers = new Map<string, () => Promise<void>>();
    vi.spyOn(vscodeStub.commands, "registerCommand").mockImplementation(((id: string, fn: () => Promise<void>) => { handlers.set(id, fn); return { dispose() {} }; }) as never);
    applied = [];
    const session = { store: { bus: BUS }, visibleSlugs: () => ["alpha", "gamma"], setVisibleBus: async (slugs: string[]) => { applied.push(slugs); } } as unknown as Session;
    registerAuthCommands({ subscriptions: [], globalState: {}, secrets: {} } as never, session);
    run = handlers.get("terraducktel.filterBusinessUnits")!;
    quickPick = vi.spyOn(vscodeStub.window as { showQuickPick: (...a: unknown[]) => Promise<unknown> }, "showQuickPick");
    errors = vi.spyOn(vscodeStub.window as { showErrorMessage: (...a: unknown[]) => Promise<unknown> }, "showErrorMessage");
  });
  afterEach(() => vi.restoreAllMocks());

  it("is registered, and switchBusinessUnit is gone", () => { expect(run).toBeTypeOf("function"); expect(handlers.has("terraducktel.switchBusinessUnit")).toBe(false); });

  it("offers every BU as a multi-select with the visible ones pre-checked", async () => {
    quickPick.mockResolvedValue(undefined);
    await run();
    const [items, opts] = quickPick.mock.calls[0] as [Pick[], { canPickMany: boolean }];
    expect(opts.canPickMany).toBe(true);
    expect(items.map((i) => [i.label, i.description, i.picked])).toEqual([["Alpha", "alpha", true], ["Beta", "beta", false], ["Gamma", "gamma", true]]);
  });

  it("applies the selected slugs", async () => {
    quickPick.mockResolvedValue([{ slug: "beta" }]);
    await run();
    expect(applied).toEqual([["beta"]]);
  });

  it("an empty selection shows an error and applies nothing", async () => {
    quickPick.mockResolvedValue([]);
    await run();
    expect(applied).toEqual([]); expect(errors).toHaveBeenCalledWith(expect.stringMatching(/at least one business unit/i));
  });

  it("cancelling changes nothing and shows no error", async () => {
    quickPick.mockResolvedValue(undefined);
    await run();
    expect(applied).toEqual([]); expect(errors).not.toHaveBeenCalled();
  });
});
