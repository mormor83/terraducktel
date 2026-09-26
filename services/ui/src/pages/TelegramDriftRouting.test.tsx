import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), put: vi.fn() },
  getToken: () => null,
  setToken: () => {},
}));

import { TelegramDriftRouting } from "./Settings";
import { api } from "../api/client";

const STATUS = {
  configured: true,
  chat_id: "-100111",
  chat_title: "Platform Ops",
  drift_chat_id: null,
  drift_alerts_enabled: true,
};

describe("Telegram drift routing", () => {
  // Block body on purpose: a function returned from beforeEach is run as the
  // test's teardown, and mockReset() returns the mock — so an expression body
  // would call api.put() after every test.
  beforeEach(() => {
    vi.mocked(api.put).mockReset();
  });

  it("defaults to the main chat and saves a different one", async () => {
    const saved = { ...STATUS, drift_chat_id: "-100222", drift_chat_title: "Drift Watch" };
    vi.mocked(api.put).mockResolvedValue({ data: saved } as any);
    const onSaved = vi.fn();
    render(<TelegramDriftRouting status={STATUS} onSaved={onSaved} />);

    const select = screen.getByLabelText("Drift alerts destination") as HTMLSelectElement;
    expect(select.value).toBe("main");
    expect(screen.getByText("Main chat (Platform Ops)")).toBeTruthy();
    expect(screen.queryByLabelText("Drift chat id")).toBeNull();

    fireEvent.change(select, { target: { value: "other" } });
    fireEvent.change(screen.getByLabelText("Drift chat id"), { target: { value: " -100222 " } });
    fireEvent.click(screen.getByRole("button", { name: "Verify & save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(api.put).toHaveBeenCalledWith("/v1/integrations/telegram/drift", {
      drift_chat_id: "-100222",
      drift_alerts_enabled: true,
    });
  });

  it("can switch drift alerts off", async () => {
    vi.mocked(api.put).mockResolvedValue({ data: { ...STATUS, drift_alerts_enabled: false } } as any);
    render(<TelegramDriftRouting status={STATUS} onSaved={() => {}} />);
    fireEvent.change(screen.getByLabelText("Drift alerts destination"), { target: { value: "off" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith("/v1/integrations/telegram/drift", {
        drift_chat_id: "",
        drift_alerts_enabled: false,
      }),
    );
  });

  it("shows a saved drift chat with its verified title", () => {
    const status = { ...STATUS, drift_chat_id: "-100222", drift_chat_title: "Drift Watch" };
    render(<TelegramDriftRouting status={status} onSaved={() => {}} />);
    expect((screen.getByLabelText("Drift alerts destination") as HTMLSelectElement).value).toBe("other");
    expect((screen.getByLabelText("Drift chat id") as HTMLInputElement).value).toBe("-100222");
    expect(screen.getByText(/Drift Watch/)).toBeTruthy();
  });

  it("surfaces the API's rejection of a chat the bot can't see", async () => {
    vi.mocked(api.put).mockImplementation(() =>
      Promise.reject({
        response: { data: { detail: "Telegram rejected the chat (400): Bad Request: chat not found" } },
      }),
    );
    const onSaved = vi.fn();
    render(<TelegramDriftRouting status={STATUS} onSaved={onSaved} />);
    fireEvent.change(screen.getByLabelText("Drift alerts destination"), { target: { value: "other" } });
    fireEvent.change(screen.getByLabelText("Drift chat id"), { target: { value: "-100999" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify & save" }));
    expect(await screen.findByText(/chat not found/)).toBeTruthy();
    expect(onSaved).not.toHaveBeenCalled();
  });
});
