import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { AccountTag } from "./AccountTag";

describe("AccountTag", () => {
  it("renders the name and keeps the id in the tooltip", () => {
    const { container } = render(<AccountTag color="red" name="Prod-Account" id="333333333333" />);
    expect(screen.getByText("Prod-Account")).toBeTruthy();
    expect(container.firstElementChild?.getAttribute("title")).toContain("333333333333");
  });

  it("renders no provider glyph when the provider is unknown", () => {
    const { container } = render(<AccountTag color="red" name="Prod-Account" />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("distinguishes two same-named accounts by provider", () => {
    // The reported bug: an Azure subscription and an AWS account both called
    // "Dev-Account" get different colours by design, which looked like one account
    // rendering inconsistently. The glyph + sr-only label is the second channel.
    const aws = render(<AccountTag color="yellow" name="Dev-Account" provider="aws" />);
    expect(aws.container.querySelector("svg")).not.toBeNull();
    expect(aws.getByText("AWS account")).toBeTruthy();
    aws.unmount();

    const azure = render(<AccountTag color="green" name="Dev-Account" provider="azure" />);
    expect(azure.getByText("Azure subscription")).toBeTruthy();
  });

  it("names every provider for screen readers", () => {
    for (const [provider, label] of [
      ["aws", "AWS account"],
      ["azure", "Azure subscription"],
      ["gcp", "GCP project"],
      ["proxmox", "Proxmox cluster"],
      ["k8s", "Kubernetes cluster"],
    ] as const) {
      const view = render(<AccountTag color="blue" name="acct" provider={provider} />);
      expect(view.getByText(label)).toBeTruthy();
      // The glyph itself stays out of the a11y tree — the label carries it.
      expect(view.container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
      view.unmount();
    }
  });

  it("keeps the colour dot as a separate channel from the glyph", () => {
    const { container } = render(<AccountTag color="green" name="Dev-Account" provider="azure" />);
    const dot = container.querySelector("span > span");
    // Dot = which account (colour), glyph = which cloud (shape).
    expect(dot?.className).toContain("rounded-full");
    expect(dot?.className).toContain("emerald");
  });
});
