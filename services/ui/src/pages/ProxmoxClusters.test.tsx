import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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
    post: vi.fn(), put: vi.fn(), delete: vi.fn(),
  },
}));

import ProxmoxClusters from "./ProxmoxClusters";

describe("ProxmoxClusters", () => {
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
});
