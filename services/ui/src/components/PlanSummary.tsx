import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import CopyButton from "./CopyButton";
import { cx } from "./ui";

type GraphNode = {
  id: string;
  address: string;
  type: string;
  name: string;
  provider: string;
  module: string;
  change: "create" | "update" | "delete" | "replace" | "no_op" | "read" | "unknown";
  mode: string;
};

type Graph = {
  nodes: GraphNode[];
  edges: unknown[];
  summary: { add: number; change: number; destroy: number; no_op?: number };
};

type Change = GraphNode["change"];

const CHANGE_PILL: Record<Change, string> = {
  create:  "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  update:  "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  delete:  "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  replace: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300",
  no_op:   "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-400",
  read:    "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  unknown: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-400",
};

const CHANGE_LABEL: Record<Change, string> = {
  create:  "create",
  update:  "update",
  delete:  "destroy",
  replace: "replace",
  no_op:   "no-op",
  read:    "read",
  unknown: "?",
};

const CHANGE_ORDER: Record<Change, number> = {
  delete: 0, replace: 1, create: 2, update: 3, read: 4, no_op: 5, unknown: 6,
};

function changeRank(c: Change): number {
  return CHANGE_ORDER[c] ?? 99;
}

// Serialize the visible plan summary as a TSV blob: header line with the
// add/change/destroy counts, then one tab-separated row per resource.
// Pasted into a spreadsheet or chat it stays readable.
function planSummaryToText(graph: Graph | null, nodes: GraphNode[]): string {
  if (!graph) return "";
  const header =
    `Plan Summary  +${graph.summary.add} ~${graph.summary.change} -${graph.summary.destroy}\n` +
    `change\taddress\ttype\tmodule\n`;
  const lines = nodes
    .map((n) => [CHANGE_LABEL[n.change], n.address, n.type, n.module || ""].join("\t"))
    .join("\n");
  return header + lines + "\n";
}

// Renders the resources table. `fullscreen` switches between the inline
// (constrained) and modal (taller) layouts — same table, different chrome.
function ResourcesTable({
  nodes,
  fullscreen,
}: {
  nodes: GraphNode[];
  fullscreen: boolean;
}) {
  return (
    <div
      className={cx(
        "overflow-auto rounded-md border border-slate-200 dark:border-slate-800",
        fullscreen ? "max-h-[calc(100vh-220px)]" : "max-h-80",
      )}
    >
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-slate-50 text-left text-[10px] uppercase tracking-wider text-slate-500 dark:bg-slate-900/80 dark:text-slate-400">
          <tr>
            <th className="px-3 py-2 font-medium">Action</th>
            <th className="px-3 py-2 font-medium">Type</th>
            <th className="px-3 py-2 font-medium">Address</th>
            <th className="px-3 py-2 font-medium">Module</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((n, i) => (
            <tr
              key={n.id}
              className={cx(
                i > 0 ? "border-t border-slate-100 dark:border-slate-800/60" : "",
                "hover:bg-slate-50 dark:hover:bg-slate-800/40",
              )}
            >
              <td className="px-3 py-1.5">
                <span
                  className={cx(
                    "inline-block rounded px-1.5 py-0.5 font-semibold uppercase tracking-wide text-[10px]",
                    CHANGE_PILL[n.change],
                  )}
                >
                  {CHANGE_LABEL[n.change]}
                </span>
              </td>
              <td className="px-3 py-1.5 font-mono text-[11px] text-slate-700 dark:text-slate-300">
                {n.type}
              </td>
              <td className="px-3 py-1.5 font-mono text-[11px] text-slate-900 dark:text-slate-100 break-all">
                {n.address}
              </td>
              <td className="px-3 py-1.5 font-mono text-[10px] text-slate-500">
                {n.module || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SummaryCounts({ summary }: { summary: Graph["summary"] }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="rounded bg-emerald-100 px-2 py-0.5 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
        + {summary.add} create
      </span>
      <span className="rounded bg-amber-100 px-2 py-0.5 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
        ~ {summary.change} change
      </span>
      <span className="rounded bg-red-100 px-2 py-0.5 text-red-800 dark:bg-red-900/40 dark:text-red-300">
        − {summary.destroy} destroy
      </span>
      {summary.no_op != null && summary.no_op > 0 && (
        <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-700 dark:bg-slate-800 dark:text-slate-400">
          · {summary.no_op} no-op
        </span>
      )}
    </div>
  );
}

export default function PlanSummary({ runId }: { runId: string }) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [showOnlyChanges, setShowOnlyChanges] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .get(`/v1/runs/${runId}/graph`)
      .then((r) => {
        if (alive) setGraph(r.data);
      })
      .catch((e: any) => {
        if (alive)
          setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load plan summary");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [runId]);

  // ESC closes fullscreen.
  useEffect(() => {
    if (!fullscreen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setFullscreen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  const filteredNodes = useMemo(() => {
    if (!graph) return [];
    let nodes = graph.nodes;
    if (showOnlyChanges) {
      nodes = nodes.filter((n) => n.change !== "no_op" && n.change !== "read");
    }
    const q = filter.trim().toLowerCase();
    if (q) {
      nodes = nodes.filter((n) =>
        [n.address, n.type, n.name, n.module].join(" ").toLowerCase().includes(q),
      );
    }
    return [...nodes].sort((a, b) => {
      const r = changeRank(a.change) - changeRank(b.change);
      return r !== 0 ? r : a.address.localeCompare(b.address);
    });
  }, [graph, filter, showOnlyChanges]);

  if (loading) {
    return (
      <p className="text-xs italic text-slate-500">Loading plan summary…</p>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
        {error}
      </div>
    );
  }

  if (!graph || graph.nodes.length === 0) {
    return (
      <p className="text-xs italic text-slate-500">
        No structured plan available yet — the executor hasn&apos;t posted a plan JSON for this run.
      </p>
    );
  }

  const controls = (
    <div className="flex flex-wrap items-center gap-3">
      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter by address, type, module…"
        className="w-64 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs dark:border-slate-700 dark:bg-slate-950"
      />
      <label className="flex cursor-pointer select-none items-center gap-1.5 text-[11px] text-slate-600 dark:text-slate-400">
        <input
          type="checkbox"
          checked={showOnlyChanges}
          onChange={(e) => setShowOnlyChanges(e.target.checked)}
          className="h-3.5 w-3.5"
        />
        Only show changes
      </label>
      <span className="text-[11px] text-slate-500">
        {filteredNodes.length} of {graph.nodes.length} resources
      </span>
    </div>
  );

  const body = (
    <div className="space-y-3">
      <SummaryCounts summary={graph.summary} />
      {controls}
      <ResourcesTable nodes={filteredNodes} fullscreen={fullscreen} />
    </div>
  );

  return (
    <>
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[10px] uppercase tracking-wider text-slate-500">
            resources in this plan
          </p>
          <div className="flex items-center gap-2">
            <CopyButton
              getText={() => planSummaryToText(graph, filteredNodes)}
              title="Copy the filtered resources table as TSV"
            />
            <button
              type="button"
              onClick={() => setFullscreen(true)}
              className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
              title="Open the resources table in a larger window"
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                <path d="M4 4h7v2H6v5H4V4Zm9 0h7v7h-2V6h-5V4ZM4 13h2v5h5v2H4v-7Zm14 0h2v7h-7v-2h5v-5Z" />
              </svg>
              Open larger
            </button>
          </div>
        </div>
        {body}
      </div>

      {fullscreen && (
        <div
          role="dialog"
          aria-label="Plan Summary"
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-10 backdrop-blur-sm"
          onClick={() => setFullscreen(false)}
        >
          <div
            className="w-full max-w-6xl rounded-lg border border-slate-200 bg-white shadow-xl dark:border-slate-700/80 dark:bg-slate-900"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
              <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Plan Summary —{" "}
                <span className="text-slate-500">
                  +{graph.summary.add} ~{graph.summary.change} −{graph.summary.destroy}
                </span>
              </h3>
              <div className="flex items-center gap-2">
                <CopyButton
                  getText={() => planSummaryToText(graph, filteredNodes)}
                  title="Copy the filtered resources table as TSV"
                />
                <button
                  type="button"
                  onClick={() => setFullscreen(false)}
                  className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                  title="Close (Esc)"
                >
                  Close
                </button>
              </div>
            </div>
            <div className="space-y-3 px-5 py-4">{body}</div>
          </div>
        </div>
      )}
    </>
  );
}
