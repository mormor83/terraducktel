import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { TagChip, TagList } from "./TagChip";

describe("TagChip", () => {
  it("renders key and value", () => {
    render(<TagChip tagKey="team" value="payments" />);
    expect(screen.getByText("team")).toBeTruthy();
    expect(screen.getByText("payments")).toBeTruthy();
  });

  it("omits the = for a valueless tag", () => {
    const { container } = render(<TagChip tagKey="deprecated" value="" />);
    expect(container.textContent).toBe("deprecated");
  });

  it("is a plain span when not clickable", () => {
    const { container } = render(<TagChip tagKey="team" value="pay" />);
    expect(container.querySelector("button")).toBeNull();
  });

  it("is a real button when clickable, so it is keyboard reachable", () => {
    render(<TagChip tagKey="team" value="pay" onClick={() => {}} />);
    expect(screen.getByRole("button")).toBeTruthy();
  });

  it("reports pressed state for screen readers", () => {
    render(<TagChip tagKey="team" value="pay" onClick={() => {}} active />);
    expect(screen.getByRole("button").getAttribute("aria-pressed")).toBe(
      "true",
    );
  });

  it("does not bubble to the row underneath", () => {
    // The row is clickable (expand/collapse); filtering must not also toggle it.
    const onRow = vi.fn();
    const onTag = vi.fn();
    render(
      <div onClick={onRow}>
        <TagChip tagKey="team" value="pay" onClick={onTag} />
      </div>,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onTag).toHaveBeenCalledWith("team", "pay");
    expect(onRow).not.toHaveBeenCalled();
  });
});

describe("TagList", () => {
  it("renders nothing when there are no tags", () => {
    const { container } = render(<TagList tags={{}} />);
    expect(container.firstChild).toBeNull();
  });

  it("survives null tags", () => {
    const { container } = render(<TagList tags={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("sorts by key so a row always reads the same way", () => {
    const { container } = render(<TagList tags={{ zebra: "1", alpha: "2" }} />);
    const text = container.textContent ?? "";
    expect(text.indexOf("alpha")).toBeLessThan(text.indexOf("zebra"));
  });

  it("collapses the overflow instead of wrapping unbounded", () => {
    render(
      <TagList tags={{ a: "1", b: "2", c: "3", d: "4", e: "5" }} max={3} />,
    );
    expect(screen.getByText("+2")).toBeTruthy();
  });

  it("puts the hidden tags in the overflow tooltip", () => {
    render(<TagList tags={{ a: "1", b: "2", c: "3", d: "4" }} max={3} />);
    expect(screen.getByText("+1").getAttribute("title")).toBe("d=4");
  });

  it("marks the chip matching the active filter", () => {
    render(
      <TagList
        tags={{ team: "pay", tier: "prod" }}
        onTagClick={() => {}}
        activeTag={{ key: "team", value: "pay" }}
      />,
    );
    const pressed = screen
      .getAllByRole("button")
      .filter((b) => b.getAttribute("aria-pressed") === "true");
    expect(pressed).toHaveLength(1);
    expect(pressed[0].textContent).toContain("pay");
  });

  it("a bare-key filter marks every value of that key", () => {
    render(
      <TagList
        tags={{ owner: "jane" }}
        onTagClick={() => {}}
        activeTag={{ key: "owner", value: null }}
      />,
    );
    expect(screen.getByRole("button").getAttribute("aria-pressed")).toBe(
      "true",
    );
  });
});
