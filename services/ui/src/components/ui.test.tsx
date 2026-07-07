import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  cx,
  Button,
  Badge,
  RunStatusBadge,
  DriftBadge,
  EmptyState,
  ConfirmDialog,
} from "./ui";

describe("cx", () => {
  it("joins truthy class names and drops falsy ones", () => {
    expect(cx("a", false, "b", undefined, null, "c")).toBe("a b c");
  });
});

describe("Button", () => {
  it("renders children and fires onClick", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    const btn = screen.getByRole("button", { name: "Go" });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("applies variant + size classes", () => {
    render(<Button variant="danger" size="sm">X</Button>);
    const cls = screen.getByRole("button").className;
    expect(cls).toContain("h-7"); // sm size
    expect(cls).toContain("#c4452f"); // danger variant hex
  });
});

describe("Badge family", () => {
  it("Badge renders its children", () => {
    render(<Badge tone="success">ok</Badge>);
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  it("RunStatusBadge humanizes status + falls back to neutral for unknown", () => {
    render(<RunStatusBadge status="awaiting_approval" />);
    expect(screen.getByText("awaiting approval")).toBeInTheDocument();
    render(<RunStatusBadge status="weird_state" />);
    expect(screen.getByText("weird state")).toBeInTheDocument();
  });

  it("DriftBadge renders the status text", () => {
    render(<DriftBadge status="drifted" />);
    expect(screen.getByText("drifted")).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("renders title, description and optional action", () => {
    render(<EmptyState title="Nothing here" description="Add one" action={<button>New</button>} />);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    expect(screen.getByText("Add one")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New" })).toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  const base = {
    title: "Delete?",
    message: "Are you sure",
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
  };

  it("renders nothing when closed", () => {
    const { container } = render(<ConfirmDialog open={false} {...base} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders when open and fires confirm/cancel via buttons", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<ConfirmDialog open title="Delete?" message="x" onConfirm={onConfirm} onCancel={onCancel} />);
    expect(screen.getByRole("dialog", { name: "Delete?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("Enter confirms, Escape cancels — unless busy", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <ConfirmDialog open title="t" message="x" onConfirm={onConfirm} onCancel={onCancel} />,
    );
    fireEvent.keyDown(window, { key: "Enter" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).toHaveBeenCalledOnce();

    // busy → keys are inert
    rerender(<ConfirmDialog open busy title="t" message="x" onConfirm={onConfirm} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: "Enter" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("backdrop click (outside the card) cancels", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog open title="t" message="x" onConfirm={vi.fn()} onCancel={onCancel} />);
    fireEvent.mouseDown(document.body);
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
