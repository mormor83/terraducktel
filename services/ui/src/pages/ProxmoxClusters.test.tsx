import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: {
    get: vi.fn(async (url: string) => {
      if (url === "/v1/proxmox-clusters") {
        return {
          data: [
            {
              id: "pmx-1", business_unit_id: "bu", slug: "home", name: "Home Lab",
              endpoint: "https://pve.local:8006", api_token_id: "tdt@pve!ci",
              token_secret_masked_tail: "…5555", ssh_username: "root", has_ssh_key: true,
              tls_insecure: true, ca_cert_pem: null, color: null, color_effective: "blue",
            },
          ],
        };
      }
      return { data: [] };
    }),
    post: vi.fn(), put: vi.fn(async () => ({ data: {} })), delete: vi.fn(),
  },
}));

import { api } from "../api/client";
import ProxmoxClusters, { normalizeEndpoint } from "./ProxmoxClusters";

async function openEdit() {
  render(<ProxmoxClusters />);
  await waitFor(() => expect(screen.getByText("Home Lab")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const endpoint = screen.getByPlaceholderText("https://pve.example.com:8006") as HTMLInputElement;
  const secret = screen.getByLabelText("API token secret") as HTMLInputElement;
  const form = screen.getByRole("button", { name: "Save" }).closest("form") as HTMLFormElement;
  return { endpoint, secret, form };
}

describe("ProxmoxClusters", () => {
  beforeEach(() => {
    vi.mocked(api.put).mockClear();
  });

  it("lists clusters with endpoint, token id and masked tail, never the secret", async () => {
    const { container } = render(<ProxmoxClusters />);
    await waitFor(() => expect(screen.getByText("Home Lab")).toBeTruthy());
    expect(screen.getByText(/https:\/\/pve\.local:8006/)).toBeTruthy();
    expect(screen.getByText(/tdt@pve!ci/)).toBeTruthy();
    expect(screen.getByText(/…5555/)).toBeTruthy();
    expect(screen.getByText(/ssh root/)).toBeTruthy();
    expect(screen.getByText(/tls verify off/)).toBeTruthy();
    expect(container.textContent).not.toContain("5555-");
  });

  it("normalizes endpoints the way the API does", () => {
    expect(normalizeEndpoint(" HTTPS://PVE.local:8006/api2/json/ ")).toBe("https://pve.local:8006");
    expect(normalizeEndpoint("pve.local:8006")).toBe("https://pve.local:8006");
  });

  it("keeps the secret optional while the endpoint is unchanged", async () => {
    const { endpoint, secret, form } = await openEdit();
    expect(secret.required).toBe(false);
    // Cosmetic re-spelling of the same endpoint is not a change.
    fireEvent.change(endpoint, { target: { value: "https://PVE.local:8006/api2/json" } });
    expect(secret.required).toBe(false);
    expect(screen.queryByText(/required — the endpoint changed/)).toBeNull();
    fireEvent.submit(form);
    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    const body = vi.mocked(api.put).mock.calls[0][1] as Record<string, unknown>;
    expect(body).not.toHaveProperty("api_token_secret");
  });

  it("requires the secret when the endpoint changes and blocks submit without it", async () => {
    const { endpoint, secret, form } = await openEdit();
    fireEvent.change(endpoint, { target: { value: "https://pve2.local:8006" } });
    expect(secret.required).toBe(true);
    expect(screen.getByText(/required — the endpoint changed/)).toBeTruthy();

    fireEvent.submit(form);
    expect(await screen.findByText(/Re-enter the API token secret/)).toBeTruthy();
    expect(api.put).not.toHaveBeenCalled();

    fireEvent.change(secret, { target: { value: "new-secret-7777" } });
    fireEvent.submit(form);
    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    const [url, body] = vi.mocked(api.put).mock.calls[0] as [string, Record<string, unknown>];
    expect(url).toBe("/v1/proxmox-clusters/pmx-1");
    expect(body.endpoint).toBe("https://pve2.local:8006");
    expect(body.api_token_secret).toBe("new-secret-7777");
  });
});
