import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Badge, Card, CardBody, EmptyState, SectionHeader, Skeleton } from "../components/ui";

type AuditEntry = {
  id: string;
  user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  workspace_id: string | null;
  details: Record<string, unknown> | null;
  created_at: string;
};

const ACTION_TONE: Record<string, "success" | "warning" | "danger" | "info" | "neutral"> = {
  approve: "success",
  reject: "danger",
  apply: "warning",
  plan: "info",
  cancel: "neutral",
  delete: "danger",
};

function actionTone(action: string) {
  for (const k of Object.keys(ACTION_TONE)) {
    if (action.toLowerCase().includes(k)) return ACTION_TONE[k];
  }
  return "neutral" as const;
}

export default function AuditLog() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/v1/audit")
      .then((r) => setEntries(r.data.items ?? []))
      .catch((e) => setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to load"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <SectionHeader eyebrow="Compliance" title="Audit log" subtitle="Every privileged action — runs, approvals, role changes." />

      {err && (
        <Card className="mb-4 border-red-900/50 bg-red-950/30">
          <CardBody className="text-sm text-red-300">{err}</CardBody>
        </Card>
      )}

      {loading ? (
        <Card>
          <CardBody className="space-y-3">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/3" />
          </CardBody>
        </Card>
      ) : entries.length === 0 ? (
        <EmptyState title="No audit entries yet" description="Privileged actions will be recorded here." />
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-brand-border text-left text-[11px] uppercase tracking-wider text-brand-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Time</th>
                  <th className="px-4 py-3 font-medium">Action</th>
                  <th className="px-4 py-3 font-medium">Resource</th>
                  <th className="px-4 py-3 font-medium">User</th>
                  <th className="px-4 py-3 font-medium">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-brand-border/70">
                {entries.map((e) => (
                  <tr key={e.id} className="transition-colors hover:bg-brand-surface2/70 dark:hover:bg-white/5">
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-400">
                      {new Date(e.created_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={actionTone(e.action)}>{e.action}</Badge>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-300">
                      {e.resource_type}:{e.resource_id.slice(0, 8)}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-500">
                      {e.user_id?.slice(0, 8) ?? "—"}
                    </td>
                    <td className="max-w-md truncate px-4 py-3 font-mono text-xs text-slate-500">
                      {e.details ? JSON.stringify(e.details) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
