import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";

import SplashScreen from "./SplashScreen";

/** jsdom has no matchMedia; default to "motion is fine". */
function stubMatchMedia(reduced = false) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reduced && query.includes("reduce"),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
    onchange: null,
  })) as unknown as typeof window.matchMedia;
}

describe("SplashScreen", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubMatchMedia();
    localStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("never intercepts pointer events, so the app underneath stays clickable", () => {
    const { container } = render(<SplashScreen />);
    const root = container.querySelector(".tdsp-root")!;
    expect(root).toBeTruthy();
    // The class carries `pointer-events:none`; assert the contract at the
    // class level since jsdom doesn't apply the injected <style>.
    expect(root.className).toContain("tdsp-root");
    expect(root.getAttribute("aria-hidden")).toBe("true");
  });

  it("hides the sigil's heading from the a11y tree", () => {
    const { container } = render(<SplashScreen />);
    // LoginSigil renders an <h1>; it must sit inside the aria-hidden curtain
    // so it can't collide with the login card's heading.
    const h1 = container.querySelector("h1")!;
    expect(h1.closest("[aria-hidden='true']")).not.toBeNull();
  });

  it("leaves after the dwell and unmounts after the fade", () => {
    const { container } = render(<SplashScreen />);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(container.querySelector(".tdsp-leaving")).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(520);
    });
    expect(container.querySelector(".tdsp-root")).toBeNull();
  });

  it("dismisses early on a click anywhere", () => {
    const { container } = render(<SplashScreen />);
    act(() => {
      window.dispatchEvent(new Event("pointerdown"));
    });
    expect(container.querySelector(".tdsp-leaving")).not.toBeNull();
  });

  it("dismisses early on a keypress", () => {
    const { container } = render(<SplashScreen />);
    act(() => {
      window.dispatchEvent(new Event("keydown"));
    });
    expect(container.querySelector(".tdsp-leaving")).not.toBeNull();
  });

  it("is skipped entirely when opted out", () => {
    localStorage.setItem("terraducktel_splash", "off");
    const { container } = render(<SplashScreen />);
    expect(container.querySelector(".tdsp-root")).toBeNull();
  });

  it("shortens the dwell under prefers-reduced-motion", () => {
    stubMatchMedia(true);
    const { container } = render(<SplashScreen />);
    act(() => {
      vi.advanceTimersByTime(700);
    });
    expect(container.querySelector(".tdsp-leaving")).not.toBeNull();
  });
});
