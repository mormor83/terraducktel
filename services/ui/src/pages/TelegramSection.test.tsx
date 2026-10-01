import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  api: { get: vi.fn().mockResolvedValue({ data: { configured: false } }), put: vi.fn() },
}));

import { TelegramSection } from "./Settings";

describe("Telegram settings help text", () => {
  it("warns that failed-run notifications send raw Terraform output to Telegram", async () => {
    render(<TelegramSection />);
    const warning = await screen.findByText(/excerpt of raw Terraform output/i);
    expect(warning.textContent).toMatch(/third-party/i);
    expect(warning.textContent).toMatch(/Telegram/);
  });
});
