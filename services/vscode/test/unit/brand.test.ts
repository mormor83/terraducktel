import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import * as vscode from "vscode";
import { initBrandIcons, statusAsset, statusIcon } from "../../src/views/brand";

const root = join(__dirname, "..", "..");
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

// Status inks from the design handoff (docs/design_handoff_ide_plugins/README.md → Design tokens).
const INK: Record<string, { dark: string; light: string }> = {
  applied: { dark: "#7ed078", light: "#2f7c34" },
  planned: { dark: "#4fb3c4", light: "#2e727e" },
  awaiting: { dark: "#e0a93b", light: "#96690f" },
  failed: { dark: "#e05c45", light: "#b3402c" },
  cancelled: { dark: "#6e857a", light: "#4f6157" },
  pending: { dark: "#6e857a", light: "#4f6157" },
  none: { dark: "#6e857a", light: "#4f6157" },
};

describe("brand status assets", () => {
  it("maps run and step statuses to the handoff's icon set", () => {
    expect(statusAsset("applied")).toBe("applied");
    expect(statusAsset("success")).toBe("applied");
    expect(statusAsset("planned")).toBe("planned");
    expect(statusAsset("awaiting_approval")).toBe("awaiting");
    expect(statusAsset("failed")).toBe("failed");
    expect(statusAsset("cancelled")).toBe("cancelled");
    expect(statusAsset("skipped")).toBe("cancelled");
    expect(statusAsset("pending")).toBe("pending");
    expect(statusAsset(undefined)).toBe("none");
    expect(statusAsset("quantum")).toBe("none");
    for (const s of ["running", "planning", "applying"]) expect(statusAsset(s)).toBeUndefined();
  });

  it.each(Object.keys(INK))("ships a 16px %s SVG per theme, stroked/filled in that theme's ink", (name) => {
    for (const theme of ["light", "dark"] as const) {
      const file = join(root, "media", "status", `${name}-${theme}.svg`);
      expect(existsSync(file), file).toBe(true);
      const svg = readFileSync(file, "utf8");
      expect(svg).toMatch(/width="16"/);
      expect(svg).toMatch(/height="16"/);
      expect(svg).toMatch(/viewBox="0 0 24 24"/);
      const colours = new Set([...svg.matchAll(/(?:stroke|fill)="(#[0-9a-f]{6})"/gi)].map((m) => m[1].toLowerCase()));
      expect([...colours]).toEqual([INK[name][theme]]);
    }
  });
});

describe("statusIcon", () => {
  beforeAll(() => initBrandIcons(vscode.Uri.file("/ext")));

  it("uses the per-theme brand SVG for a settled status", () => {
    const icon = statusIcon("failed") as { light: { path: string }; dark: { path: string } };
    expect(icon.light.path).toBe("/ext/media/status/failed-light.svg");
    expect(icon.dark.path).toBe("/ext/media/status/failed-dark.svg");
  });

  it("spins a sync codicon tinted with terraducktel.run while a run is in flight", () => {
    const icon = statusIcon("applying") as vscode.ThemeIcon;
    expect(icon).toBeInstanceOf(vscode.ThemeIcon);
    expect(icon.id).toBe("sync~spin");
    expect((icon.color as vscode.ThemeColor).id).toBe("terraducktel.run");
  });
});

describe("terraducktel.* colour contributions", () => {
  const colours = Object.fromEntries((pkg.contributes.colors as Array<{ id: string; defaults: Record<string, string> }>).map((c) => [c.id, c.defaults]));

  it("contributes every brand colour with light, dark and high-contrast defaults", () => {
    const ids = ["add", "change", "destroy", "replace", "run", "accent", "addBackground", "changeBackground", "destroyBackground", "replaceBackground"].map((k) => `terraducktel.${k}`);
    for (const id of ids) {
      expect(colours[id], id).toBeDefined();
      for (const k of ["light", "dark", "highContrast", "highContrastLight"]) expect(colours[id][k], `${id}.${k}`).toMatch(/^#[0-9a-f]{6}([0-9a-f]{2})?$/i);
    }
  });

  it("uses the handoff's diff and accent values", () => {
    expect(colours["terraducktel.add"]).toMatchObject({ dark: "#7ed078", light: "#2f9b56" });
    expect(colours["terraducktel.change"]).toMatchObject({ dark: "#e0a93b", light: "#c98a14" });
    expect(colours["terraducktel.destroy"]).toMatchObject({ dark: "#e05c45", light: "#c4452f" });
    expect(colours["terraducktel.replace"]).toMatchObject({ dark: "#e0a93b", light: "#c98a14" });
    expect(colours["terraducktel.accent"]).toMatchObject({ dark: "#b6ff4b", light: "#1f6f6c" });
    expect(colours["terraducktel.run"]).toMatchObject({ dark: "#b6ff4b", light: "#5c7a12" });
    // rgba(126,208,120,.12) → alpha 0x1f; light values are opaque tints.
    expect(colours["terraducktel.addBackground"]).toMatchObject({ dark: "#7ed0781f", light: "#e6f5ec" });
    expect(colours["terraducktel.changeBackground"]).toMatchObject({ dark: "#e0a93b21", light: "#fbf2dc" });
    expect(colours["terraducktel.destroyBackground"]).toMatchObject({ dark: "#e05c4521", light: "#fbe7e2" });
    expect(colours["terraducktel.replaceBackground"]).toMatchObject({ dark: "#e0a93b2e", light: "#fbf2dc" });
  });
});
