import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const currentUser = { value: { id: "u1", email: "a@x", role: "admin", is_superadmin: true, name: "A" } };
vi.mock("../../hooks/useAuth", () => ({
  useCurrentUser: () => currentUser.value,
  hasMinRole: () => true,
}));

import { api } from "../../api/client";
import StateStoreSection from "./StateStoreSection";

const UNCONFIGURED = {
  configured: false, partial: false, access_key_id_tail: null, secret_access_key_tail: null,
  bucket: "terraducktel-state", endpoint_url: "https://s3.example.internal:3900",
  use_localstack: false, insecure_endpoint: false,
};
const CONFIGURED = { ...UNCONFIGURED, configured: true, access_key_id_tail: "…WXYZ", secret_access_key_tail: "…9876" };

describe("StateStoreSection", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.delete).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: CONFIGURED } as any);
    currentUser.value = { ...currentUser.value, is_superadmin: true };
  });

  it("shows only masked tails and the env-side endpoint", async () => {
    render(<StateStoreSection />);
    expect(await screen.findByText("…WXYZ")).toBeInTheDocument();
    expect(screen.getByText("…9876")).toBeInTheDocument();
    expect(screen.getByText("https://s3.example.internal:3900")).toBeInTheDocument();
    expect(screen.getByText("configured")).toBeInTheDocument();
  });

  it("saves both halves together via PUT and clears the inputs", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: UNCONFIGURED } as any);
    vi.mocked(api.put).mockResolvedValue({ data: CONFIGURED } as any);
    render(<StateStoreSection />);
    const save = await screen.findByRole("button", { name: "Save key pair" });
    fireEvent.change(screen.getByLabelText("Access key ID"), { target: { value: "GK1234WXYZ" } });
    expect(save).toBeDisabled(); // secret still empty
    fireEvent.change(screen.getByLabelText("Secret access key"), { target: { value: "secret-9876" } });
    fireEvent.click(save);
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith("/v1/integrations/state-store", {
        access_key_id: "GK1234WXYZ", secret_access_key: "secret-9876",
      }),
    );
    expect(await screen.findByText("…WXYZ")).toBeInTheDocument();
    expect(screen.getByLabelText("Access key ID")).toHaveValue("");
    expect(screen.getByLabelText("Secret access key")).toHaveValue("");
  });

  it("removes the key pair via DELETE", async () => {
    vi.mocked(api.delete).mockResolvedValue({ data: null } as any);
    render(<StateStoreSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove key pair" }));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith("/v1/integrations/state-store"));
  });

  it("warns about a plaintext endpoint and a half-configured pair", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { ...UNCONFIGURED, partial: true, access_key_id_tail: "…WXYZ",
        endpoint_url: "http://garage.internal:3900", insecure_endpoint: true },
    } as any);
    render(<StateStoreSection />);
    expect(await screen.findByText(/plaintext http:\/\//)).toBeInTheDocument();
    expect(screen.getByText(/Only one half of the key pair/)).toBeInTheDocument();
    expect(screen.getByText("incomplete")).toBeInTheDocument();
  });

  it("is read-only for non-superadmins", async () => {
    currentUser.value = { ...currentUser.value, is_superadmin: false };
    render(<StateStoreSection />);
    await screen.findByText("…WXYZ");
    expect(screen.queryByLabelText("Access key ID")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save key pair" })).toBeNull();
    expect(screen.getByText(/Only a superadmin/)).toBeInTheDocument();
  });
});
