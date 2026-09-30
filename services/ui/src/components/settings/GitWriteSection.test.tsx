import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("../../hooks/useBusinessUnit", () => ({
  BU_CHANGED_EVENT: "terraducktel:bu-changed",
  useBusinessUnitSelection: () => ["default", () => {}],
}));

const currentUser = { value: { id: "u1", email: "a@x", role: "admin", is_superadmin: true, name: "A" } };
vi.mock("../../hooks/useAuth", () => ({
  useCurrentUser: () => currentUser.value,
  hasMinRole: () => true,
}));

import { api } from "../../api/client";
import GitWriteSection from "./GitWriteSection";

const STATE = {
  enabled: false, token_configured: true, token_source: "github", token_tail: "…abcd",
  username: null, bot_name: "Terraducktel", bot_email: "bot@x",
};

describe("GitWriteSection", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: STATE } as any);
    currentUser.value = { ...currentUser.value, is_superadmin: true };
  });

  it("renders the current state without exposing the token", async () => {
    render(<GitWriteSection />);
    expect(await screen.findByText("using this BU's GitHub token")).toBeInTheDocument();
    expect(screen.getByText("…abcd")).toBeInTheDocument();
    expect(screen.getByText("disabled")).toBeInTheDocument();
    expect(screen.getByLabelText("Allow promotions to push commits")).not.toBeChecked();
  });

  it("saves a dedicated token via PUT", async () => {
    vi.mocked(api.put).mockResolvedValue({ data: { ...STATE, token_source: "dedicated", token_tail: "…9999" } } as any);
    render(<GitWriteSection />);
    fireEvent.change(await screen.findByLabelText("Dedicated write token (optional)"), { target: { value: "tok-9999" } });
    fireEvent.click(screen.getByRole("button", { name: "Save token" }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith("/v1/integrations/git-write", { token: "tok-9999" }));
    expect(await screen.findByText("dedicated token")).toBeInTheDocument();
    expect(screen.getByLabelText("Dedicated write token (optional)")).toHaveValue("");
  });

  it("enabling sends enabled=true", async () => {
    vi.mocked(api.put).mockResolvedValue({ data: { ...STATE, enabled: true } } as any);
    render(<GitWriteSection />);
    fireEvent.click(await screen.findByLabelText("Allow promotions to push commits"));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith("/v1/integrations/git-write", { enabled: true }));
  });

  it("renders per-repo push test results", async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: {
        enabled: true, token_configured: true, ok: false,
        repos: [
          { repo_url: "https://github.com/acme/a", ok: true, can_push: true, detail: null },
          { repo_url: "https://github.com/acme/b", ok: true, can_push: false, detail: "The token can read this repository but cannot push to it" },
        ],
      },
    } as any);
    render(<GitWriteSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Test push access" }));
    expect(await screen.findByText("https://github.com/acme/b")).toBeInTheDocument();
    expect(screen.getByText(/cannot push to it/)).toBeInTheDocument();
    expect(screen.getByText("✓")).toBeInTheDocument();
    expect(screen.getByText("✗")).toBeInTheDocument();
  });

  it("is read-only for non-BU-admins", async () => {
    currentUser.value = { ...currentUser.value, is_superadmin: false };
    render(<GitWriteSection />);
    await screen.findByText("using this BU's GitHub token");
    expect(screen.getByLabelText("Allow promotions to push commits")).toBeDisabled();
    expect(screen.queryByLabelText("Dedicated write token (optional)")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save token" })).toBeNull();
  });
});
