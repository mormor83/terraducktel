import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { Skeleton, cx } from "./ui";

type Node = {
  id: string;
  address: string;
  type: string;
  name: string;
  provider: string;
  module: string;
  change: "create" | "update" | "delete" | "replace" | "no_op" | "read" | "unknown";
  mode: string;
};
type Edge = { source: string; target: string; kind: string };
type Graph = {
  nodes: Node[];
  edges: Edge[];
  summary: { add: number; change: number; destroy: number; no_op?: number };
};

type Pos = { x: number; y: number };

const CHANGE_COLOR: Record<Node["change"], string> = {
  create: "#10b981",   // emerald
  update: "#f59e0b",   // amber
  delete: "#ef4444",   // red
  replace: "#a855f7",  // violet
  no_op: "#94a3b8",    // slate-400
  read: "#0ea5e9",     // sky
  unknown: "#64748b",
};

const CHANGE_LABEL: Record<Node["change"], string> = {
  create: "+",
  update: "~",
  delete: "−",
  replace: "↻",
  no_op: "·",
  read: "?",
  unknown: "·",
};

// ─── force-directed layout (very small) ────────────────────────────────────

function layout(nodes: Node[], edges: Edge[], width: number, height: number): Map<string, Pos> {
  const pos = new Map<string, Pos>();
  // Group by module so same module clusters together; within a group, lay out
  // on a grid, then run a few iterations of repulsion + edge spring.
  const groups = new Map<string, Node[]>();
  for (const n of nodes) {
    const g = n.module || "(root)";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g)!.push(n);
  }
  const groupKeys = [...groups.keys()].sort();
  const cols = Math.max(1, Math.ceil(Math.sqrt(groupKeys.length)));
  const groupW = width / cols;
  const groupH = height / Math.ceil(groupKeys.length / cols);

  groupKeys.forEach((g, gi) => {
    const gx = (gi % cols) * groupW + groupW / 2;
    const gy = Math.floor(gi / cols) * groupH + groupH / 2;
    const items = groups.get(g)!;
    const r = Math.min(groupW, groupH) * 0.35;
    items.forEach((n, i) => {
      const angle = (i / Math.max(1, items.length)) * 2 * Math.PI;
      pos.set(n.id, { x: gx + Math.cos(angle) * r, y: gy + Math.sin(angle) * r });
    });
  });

  // 80 iterations of simple force-directed refinement.
  const k = 60;          // ideal edge length
  const repulse = 1500;  // strength of node-node repulsion
  const adjacency = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, new Set());
    if (!adjacency.has(e.target)) adjacency.set(e.target, new Set());
    adjacency.get(e.source)!.add(e.target);
    adjacency.get(e.target)!.add(e.source);
  }

  for (let it = 0; it < 80; it++) {
    const force = new Map<string, Pos>();
    for (const n of nodes) force.set(n.id, { x: 0, y: 0 });

    // Repulsion
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = pos.get(nodes[i].id)!;
        const b = pos.get(nodes[j].id)!;
        const dx = a.x - b.x, dy = a.y - b.y;
        const d2 = dx * dx + dy * dy + 0.01;
        const d = Math.sqrt(d2);
        const fr = repulse / d2;
        const fx = (dx / d) * fr, fy = (dy / d) * fr;
        force.get(nodes[i].id)!.x += fx;
        force.get(nodes[i].id)!.y += fy;
        force.get(nodes[j].id)!.x -= fx;
        force.get(nodes[j].id)!.y -= fy;
      }
    }
    // Spring on edges
    for (const e of edges) {
      const a = pos.get(e.source);
      const b = pos.get(e.target);
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy + 0.01);
      const f = (d - k) * 0.05;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      force.get(e.source)!.x += fx;
      force.get(e.source)!.y += fy;
      force.get(e.target)!.x -= fx;
      force.get(e.target)!.y -= fy;
    }
    // Apply with damping; clamp to canvas
    const damp = 0.4;
    for (const n of nodes) {
      const p = pos.get(n.id)!;
      const f = force.get(n.id)!;
      p.x = Math.max(40, Math.min(width - 40, p.x + f.x * damp));
      p.y = Math.max(40, Math.min(height - 40, p.y + f.y * damp));
    }
  }
  return pos;
}

// ─── component ─────────────────────────────────────────────────────────────

export default function PlanCanvas({ runId }: { runId: string }) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [hover, setHover] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showUnchanged, setShowUnchanged] = useState(true);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    let alive = true;
    api.get(`/v1/runs/${runId}/graph`)
      .then((r) => { if (alive) setGraph(r.data); })
      .catch((e: any) => { if (alive) setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load graph"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [runId]);

  const width = 1200, height = 720;

  const filtered = useMemo(() => {
    if (!graph) return null;
    let nodes = graph.nodes;
    if (!showUnchanged) {
      nodes = nodes.filter((n) => n.change !== "no_op" && n.change !== "read");
    }
    if (filter.trim()) {
      const q = filter.trim().toLowerCase();
      nodes = nodes.filter((n) =>
        [n.address, n.type, n.name, n.module].join(" ").toLowerCase().includes(q),
      );
    }
    const ids = new Set(nodes.map((n) => n.id));
    const edges = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
    return { ...graph, nodes, edges };
  }, [graph, filter, showUnchanged]);

  const positions = useMemo(() => {
    if (!filtered) return null;
    return layout(filtered.nodes, filtered.edges, width, height);
  }, [filtered]);

  if (loading) return <Skeleton className="h-[600px] w-full" />;
  if (error) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
        {error}
      </div>
    );
  }
  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="rounded-md border border-slate-200 bg-slate-50 p-6 text-center text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400">
        No structured plan available yet. The visualization populates after the
        executor runs <code className="font-mono">terraform plan</code> and PATCHes the
        run with the JSON plan output.
      </div>
    );
  }

  const sel = selected ? graph.nodes.find((n) => n.id === selected) : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter resources by address, type, module…"
          className="w-72 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950"
        />
        <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400 cursor-pointer">
          <input
            type="checkbox"
            checked={showUnchanged}
            onChange={(e) => setShowUnchanged(e.target.checked)}
            className="h-3.5 w-3.5"
          />
          Show unchanged resources
        </label>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className="text-slate-500">{filtered?.nodes.length} of {graph.nodes.length} resources</span>
          <span className="rounded bg-emerald-100 px-2 py-0.5 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">+ {graph.summary.add} create</span>
          <span className="rounded bg-amber-100 px-2 py-0.5 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">~ {graph.summary.change} change</span>
          <span className="rounded bg-red-100 px-2 py-0.5 text-red-800 dark:bg-red-900/40 dark:text-red-300">− {graph.summary.destroy} destroy</span>
          {graph.summary.no_op != null && graph.summary.no_op > 0 && (
            <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-700 dark:bg-slate-800 dark:text-slate-400">· {graph.summary.no_op} no-op</span>
          )}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-[1fr_280px]">
        <div className="rounded-md border border-slate-200 bg-white dark:border-slate-800/80 dark:bg-slate-950/60 overflow-hidden">
          <svg
            ref={svgRef}
            viewBox={`0 0 ${width} ${height}`}
            className="h-[720px] w-full"
            onClick={() => setSelected(null)}
          >
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
              </marker>
              <marker id="arrow-dim" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#cbd5e133" />
              </marker>
            </defs>
            {/* edges */}
            {filtered && positions && filtered.edges.map((e, i) => {
              const a = positions.get(e.source);
              const b = positions.get(e.target);
              if (!a || !b) return null;
              const dim = hover && hover !== e.source && hover !== e.target;
              // Shorten the line so the arrowhead lands on the node edge, not its center.
              const dx = b.x - a.x, dy = b.y - a.y;
              const len = Math.sqrt(dx * dx + dy * dy) || 1;
              const r = 14;
              const x2 = b.x - (dx / len) * r;
              const y2 = b.y - (dy / len) * r;
              return (
                <line
                  key={i}
                  x1={a.x}
                  y1={a.y}
                  x2={x2}
                  y2={y2}
                  stroke={dim ? "#cbd5e133" : "#94a3b888"}
                  strokeWidth={1.2}
                  markerEnd={dim ? "url(#arrow-dim)" : "url(#arrow)"}
                />
              );
            })}
            {/* nodes */}
            {filtered && positions && filtered.nodes.map((n) => {
              const p = positions.get(n.id)!;
              const color = CHANGE_COLOR[n.change];
              const label = CHANGE_LABEL[n.change];
              const isHover = hover === n.id;
              const isSelected = selected === n.id;
              return (
                <g
                  key={n.id}
                  transform={`translate(${p.x},${p.y})`}
                  onMouseEnter={() => setHover(n.id)}
                  onMouseLeave={() => setHover(null)}
                  onClick={(e) => { e.stopPropagation(); setSelected(n.id); }}
                  className="cursor-pointer"
                >
                  <circle
                    r={isSelected ? 16 : isHover ? 14 : 12}
                    fill={color}
                    stroke={isSelected ? "#0f172a" : "white"}
                    strokeWidth={isSelected ? 2.5 : 1.5}
                    opacity={hover && !isHover && !isSelected ? 0.4 : 1}
                  />
                  <text textAnchor="middle" dy={4} fontSize="11" fontWeight="700" fill="white" pointerEvents="none">
                    {label}
                  </text>
                  {(isHover || isSelected || filtered.nodes.length <= 30) && (
                    <text
                      textAnchor="middle"
                      dy={28}
                      fontSize="10"
                      fill="currentColor"
                      className="text-slate-700 dark:text-slate-300"
                      pointerEvents="none"
                    >
                      {n.name || n.type}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        </div>

        {/* selection panel */}
        <aside className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm dark:border-slate-800/80 dark:bg-slate-900/40">
          {sel ? (
            <div className="space-y-2">
              <div>
                <p className="text-[10px] uppercase tracking-wider text-slate-500">Address</p>
                <p className="font-mono text-xs break-all">{sel.address}</p>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <Meta label="Type" value={sel.type} />
                <Meta label="Provider" value={sel.provider.replace("registry.terraform.io/hashicorp/", "")} />
                <Meta
                  label="Change"
                  value={
                    <span className={cx(
                      "rounded px-1.5 py-0.5 font-semibold",
                      sel.change === "create" && "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
                      sel.change === "update" && "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
                      sel.change === "delete" && "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
                      sel.change === "replace" && "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300",
                      (sel.change === "no_op" || sel.change === "read") && "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-400",
                    )}>
                      {sel.change}
                    </span>
                  }
                />
                <Meta label="Mode" value={sel.mode} />
                {sel.module && <Meta label="Module" value={<code className="font-mono text-[10px]">{sel.module}</code>} />}
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-slate-500">Click a node to inspect a resource.</p>
              <div className="space-y-1.5">
                <Legend color={CHANGE_COLOR.create} label="create" />
                <Legend color={CHANGE_COLOR.update} label="update" />
                <Legend color={CHANGE_COLOR.delete} label="delete" />
                <Legend color={CHANGE_COLOR.replace} label="replace" />
                <Legend color={CHANGE_COLOR.no_op} label="no-op" />
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: any }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className="break-all">{value || "—"}</p>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span style={{ background: color }} className="inline-block h-3 w-3 rounded-full" />
      <span>{label}</span>
    </div>
  );
}
