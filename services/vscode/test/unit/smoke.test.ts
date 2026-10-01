import { describe, expect, it } from "vitest";
import { EXTENSION_ID } from "../../src/ids";

describe("scaffold", () => {
  it("exposes the extension id", () => {
    expect(EXTENSION_ID).toBe("terraducktel.terraducktel-vscode");
  });
});
