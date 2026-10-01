import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as vscodeStub from "./vscode-stub";
import { RunOutputManager } from "../../src/output/runOutput";
import { OUTPUT_LANGUAGE } from "../../src/ids";

const root = join(__dirname, "..", "..");
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

describe("terraducktel-output language", () => {
  it("is registered with a TextMate grammar", () => {
    expect(OUTPUT_LANGUAGE).toBe("terraducktel-output");
    expect(pkg.contributes.languages).toContainEqual(expect.objectContaining({ id: OUTPUT_LANGUAGE }));
    expect(pkg.contributes.grammars).toContainEqual({ language: OUTPUT_LANGUAGE, scopeName: "source.terraducktel-output", path: "./syntaxes/terraducktel-output.tmLanguage.json" });
  });

  const grammar = JSON.parse(readFileSync(join(root, "syntaxes", "terraducktel-output.tmLanguage.json"), "utf8"));
  /** Scope stack a line gets from the first grammar pattern whose `match` covers the whole line. */
  function scopes(line: string): string[] {
    for (const p of grammar.patterns as Array<{ match: string; name?: string; captures?: Record<string, { name: string }> }>) {
      const m = new RegExp(p.match).exec(line);
      if (m && m[0] === line) return [p.name, p.captures?.["0"]?.name].filter((s): s is string => !!s);
    }
    return [];
  }

  it.each([
    ["── Plan [success]", "markup.inserted"],
    ["── Apply [applied]", "markup.inserted"],
    ["── run planned", "markup.inserted"],
    ["── Plan [failed]", "markup.deleted"],
    ["── run cancelled", "markup.deleted"],
    ["── run awaiting_approval", "markup.changed"],
    ["── Checkov [skipped]", "comment"],
  ])("tints %s as %s under a bold heading scope", (line, tone) => {
    const [outer, inner] = scopes(line);
    expect(grammar.scopeName).toBe("source.terraducktel-output");
    expect(outer).toMatch(/^markup\.heading\./);
    expect(inner).toMatch(new RegExp(`^${tone.replace(".", "\\.")}\\.`));
  });

  it("gives running headers the heading scope alone", () => {
    expect(scopes("── Plan [running]")).toEqual([expect.stringMatching(/^markup\.heading\./)]);
  });

  it("marks tail errors and leaves step output alone", () => {
    expect(scopes("✕ boom")[0]).toMatch(/^markup\.deleted\./);
    expect(scopes("aws_s3_bucket.b: Creating...")).toEqual([]);
    expect(scopes("── not a header")).toEqual([]);
  });
});

describe("RunOutputManager", () => {
  afterEach(() => { vi.restoreAllMocks(); });
  it("creates each run's channel in the terraducktel-output language", () => {
    const w = vscodeStub.window as Record<string, unknown>;
    const create = vi.fn(() => ({ appendLine() {}, append() {}, show() {}, clear() {}, dispose() {} }));
    w.createOutputChannel = create;
    w.withProgress = () => new Promise(() => {});   // never runs the tail — only the channel matters here
    const out = new RunOutputManager();
    out.watch({} as never, "abcdef1234", "vpc");
    expect(create).toHaveBeenCalledWith("TDT run abcdef12 — vpc", "terraducktel-output");
    out.dispose();
  });
});
