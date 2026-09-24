import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), put: vi.fn() },
  getToken: () => null,
  setToken: () => {},
}));

import { SlackDriftRouting } from "./Settings";
import { api } from "../api/client";

const STATUS = {
  configured: true,
  channel_id: "C-DEFAULT",
  channel_name: "tdt-runs",
  drift_channel_id: null,
  drift_alerts_enabled: true,
};
const CHANNELS = [
  { id: "C-DRIFT", name: "drift-alerts", is_private: false },
  { id: "C-SEC", name: "secops", is_private: true },
];

describe("Slack drift routing", () => {
  beforeEach(() => vi.mocked(api.put).mockReset());

  it("defaults to the notification channel and saves a different one", async () => {
    const saved = { ...STATUS, drift_channel_id: "C-DRIFT", drift_channel_name: "drift-alerts" };
    vi.mocked(api.put).mockResolvedValue({ data: saved } as any);
    const onSaved = vi.fn();
    render(<SlackDriftRouting status={STATUS} channels={CHANNELS} onSaved={onSaved} />);

    const select = screen.getByLabelText("Drift alerts destination") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(screen.getByText("Default channel (#tdt-runs)")).toBeTruthy();

    fireEvent.change(select, { target: { value: "C-DRIFT" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(api.put).toHaveBeenCalledWith("/v1/integrations/slack/drift", {
      drift_channel_id: "C-DRIFT",
      drift_channel_name: "drift-alerts",
      drift_alerts_enabled: true,
    });
  });

  it("can switch drift alerts off", async () => {
    vi.mocked(api.put).mockResolvedValue({ data: { ...STATUS, drift_alerts_enabled: false } } as any);
    render(<SlackDriftRouting status={STATUS} channels={null} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Drift alerts destination"), { target: { value: "__off__" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith("/v1/integrations/slack/drift", {
        drift_channel_id: "",
        drift_channel_name: null,
        drift_alerts_enabled: false,
      }),
    );
  });

  it("shows a saved drift channel before the channel list is loaded", () => {
    const status = { ...STATUS, drift_channel_id: "C-SEC", drift_channel_name: "secops" };
    render(<SlackDriftRouting status={status} channels={null} onSaved={() => {}} />);
    expect((screen.getByLabelText("Drift alerts destination") as HTMLSelectElement).value).toBe("C-SEC");
  });
});
