// Single-select tree picker for one side of an environment link.
//
// Same account → region → folder → stack shape and filter box as the
// Dashboard, but read-only: rows are radio choices, not action rows. Helm
// nodes (a Helm stack, or a subtree holding only Helm stacks) are disabled —
// the v1 diff engine is terraform-only. When `lockLevel` is set (the other
// side is already picked), nodes at any other level are disabled too, since a
// link needs both sides at the same level.

import { useMemo, useState } from "react";

import type { EnvLevel, EnvNode } from "../../api/envLinks";
import type { AccountBadge } from "../../hooks/useAccountColors";
import { AccountTag } from "../AccountTag";
import { Badge, Input, cx } from "../ui";
import { ChevronIcon, FileIcon, FolderIcon, HelmChip } from "../workspace-tree/icons";
import type { Workspace } from "../workspace-tree/types";
import { accountIdOf, buildPathTree, filterTree, type PathNode } from "./nodeTree";

const LEVEL_LABEL: Record<EnvLevel, string> = {
  account: "account",
  region: "region",
  folder: "folder",
  stack: "stack",
};

export function EnvTreePicker({
  label,
  workspaces,
  accountBadge,
  value,
  onChange,
  lockLevel,
  disabledPath,
}: {
  label: string;
  workspaces: Workspace[];
  accountBadge: (accountId: string) => AccountBadge | null;
  value: EnvNode | null;
  onChange: (node: EnvNode) => void;
  lockLevel?: EnvLevel | null;
  /** The other side's pick — can't pick it, or anything overlapping it. */
  disabledPath?: string | null;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Set<string>>(() => {
    // Start with the current pick's ancestors expanded so it's visible.
    const s = new Set<string>();
    if (value) {
      const segs = value.path.split("/");
      for (let i = 1; i < segs.length; i++) s.add(segs.slice(0, i).join("/"));
    }
    return s;
  });

  const tree = useMemo(() => buildPathTree(workspaces), [workspaces]);
  const shown = useMemo(
    () =>
      filterTree(tree, query, (n) => {
        const id = accountIdOf(n.path);
        return id ? `${id} ${accountBadge(id)?.name ?? ""}` : "";
      }),
    [tree, query, accountBadge],
  );
  const filtering = query.trim().length > 0;

  const toggle = (path: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const overlaps = (path: string) =>
    !!disabledPath &&
    (path === disabledPath || path.startsWith(disabledPath + "/") || disabledPath.startsWith(path + "/"));

  const renderNode = (n: PathNode, depth: number) => {
    const helmOnly = n.terraformCount === 0 && n.helmCount > 0;
    const wrongLevel = !!lockLevel && lockLevel !== n.level;
    const blocked = helmOnly || wrongLevel || overlaps(n.path);
    const selected = value?.path === n.path;
    const expandable = n.children.length > 0;
    const isOpen = filtering || open.has(n.path);
    const acctId = n.level === "account" ? accountIdOf(n.path) : null;
    const badge = acctId ? accountBadge(acctId) : null;
    const why = helmOnly
      ? "Helm environments — coming soon"
      : wrongLevel
        ? `Pick a ${lockLevel} to match the other side`
        : overlaps(n.path)
          ? "Overlaps the other side"
          : undefined;

    return (
      <li key={n.path}>
        <div
          className={cx(
            "flex items-center gap-2 py-1.5 pr-3 text-[13px]",
            selected && "bg-[rgba(var(--td-glow-rgb),0.12)]",
          )}
          style={{ paddingLeft: 10 + depth * 18 }}
        >
          {expandable ? (
            <button
              type="button"
              onClick={() => toggle(n.path)}
              aria-label={isOpen ? `Collapse ${n.name}` : `Expand ${n.name}`}
              className="grid h-5 w-5 place-items-center rounded hover:bg-[rgba(var(--td-edge-rgb),0.08)]"
            >
              <ChevronIcon open={isOpen} />
            </button>
          ) : (
            <span className="w-5" />
          )}
          <label
            title={why}
            className={cx(
              "flex min-w-0 flex-1 items-center gap-2",
              blocked ? "cursor-not-allowed opacity-45" : "cursor-pointer",
            )}
          >
            <input
              type="radio"
              name={`env-picker-${label}`}
              checked={selected}
              disabled={blocked}
              onChange={() => onChange({ level: n.level, path: n.path })}
              className="accent-[var(--td-rail)]"
            />
            {n.level === "stack" ? <FileIcon /> : <FolderIcon open={isOpen && expandable} />}
            {badge ? (
              <AccountTag color={badge.color} name={badge.name} id={badge.id} provider="aws" />
            ) : (
              <span className="truncate font-mono text-[12.5px] text-brand-text">{n.name}</span>
            )}
            {acctId && <span className="font-mono text-[11px] text-brand-muted">{acctId}</span>}
            {n.stack?.kind === "helm" && <HelmChip />}
            {n.stack?.repo_ref && n.stack.kind !== "helm" && (
              <span className="truncate font-mono text-[11px] text-brand-muted">⎇ {n.stack.repo_ref}</span>
            )}
            {helmOnly && n.level !== "stack" && (
              <span className="font-mono text-[10.5px] text-brand-muted">helm · coming soon</span>
            )}
          </label>
          <Badge tone="neutral" className="shrink-0">{LEVEL_LABEL[n.level]}</Badge>
          {n.level !== "stack" && (
            <span className="w-8 shrink-0 text-right font-mono text-[11px] text-brand-muted">
              {n.terraformCount}
            </span>
          )}
        </div>
        {expandable && isOpen && <ul>{n.children.map((c) => renderNode(c, depth + 1))}</ul>}
      </li>
    );
  };

  return (
    <div className="flex min-w-0 flex-col rounded-[14px] border border-brand-border bg-brand-surface">
      <div className="border-b border-brand-border p-3">
        <p className="mb-2 font-mono text-[10px] font-medium uppercase tracking-[1.3px] text-brand-muted">
          {label}
        </p>
        <Input
          placeholder="Filter by path, account, branch…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label={`Filter ${label}`}
        />
        {value && (
          <p className="mt-2 truncate font-mono text-[11.5px] text-[var(--td-green-ink)]" title={value.path}>
            {value.level} · {value.path}
          </p>
        )}
      </div>
      <ul className="max-h-[420px] overflow-y-auto py-1" role="tree" aria-label={label}>
        {shown.length === 0 ? (
          <li className="px-4 py-6 text-center text-[12.5px] text-brand-muted">No matching stacks</li>
        ) : (
          shown.map((n) => renderNode(n, 0))
        )}
      </ul>
    </div>
  );
}
