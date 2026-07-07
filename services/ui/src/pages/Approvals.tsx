import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Button,
  Card,
  CardBody,
  EmptyState,
  RunStatusBadge,
  SectionHeader,
  cx,
} from "../components/ui";

type Run = {
  id: string;
  status: string;
  workspace_id: string;
  command: string;
  plan_output?: string | null;
};

type Tab = "plan" | null;

export default function Approvals() {
  const [pending, setPending] = useState<Run[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(null);
  const [planOutput, setPlanOutput] = useState<string>("");

  function load() {
    api.get("/v1/runs").then((r) => {
      const list = (r.data as Run[]).filter((x) => x.status === "awaiting_approval");
      setPending(list);
    });
  }

  useEffect(() => {
    load();
  }, []);

  async function approve(runId: string) {
    try {
      await api.post(`/v1/runs/${runId}/approve`, { comment: "Approved from UI" });
      setMsg("Run approved");
      setPending((p) => p.filter((x) => x.id !== runId));
    } catch (err: any) {
      setMsg(err?.response?.data?.detail ?? "Approval failed");
    }
  }

  async function reject(runId: string) {
    try {
      await api.post(`/v1/runs/${runId}/reject`, { comment: "Rejected from UI" });
      setMsg("Run rejected");
      setPending((p) => p.filter((x) => x.id !== runId));
    } catch (err: any) {
      setMsg(err?.response?.data?.detail ?? "Rejection failed");
    }
  }

  async function showPlan(runId: string) {
    if (expandedId === runId && tab === "plan") {
      setExpandedId(null);
      setTab(null);
      return;
    }
    const r = await api.get(`/v1/runs/${runId}/plan`);
    setPlanOutput(r.data.plan_output || "(no plan output)");
    setExpandedId(runId);
    setTab("plan");
  }

  return (
    <div>
      <SectionHeader
        title="Approvals"
        subtitle="Review and approve plans before they apply. Any operator+ user can approve."
      />

      {msg && (
        <Card className="mb-4 border-emerald-900/40 bg-emerald-950/30">
          <CardBody data-testid="approval-success" className="text-sm text-emerald-300">
            {msg}
          </CardBody>
        </Card>
      )}

      {pending.length === 0 ? (
        <EmptyState
          title="Nothing awaiting approval"
          description="Runs that finish planning will appear here for review."
        />
      ) : (
        <div className="space-y-4">
          {pending.map((run) => (
            <Card key={run.id} data-testid="pending-approval-card">
              <CardBody className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <code className="font-mono text-xs text-slate-500">{run.id.slice(0, 8)}</code>
                    <span className="rounded bg-slate-800/80 px-2 py-0.5 text-[11px] font-medium text-slate-300 ring-1 ring-inset ring-slate-700/50">
                      {run.command}
                    </span>
                    <RunStatusBadge status={run.status} />
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant={expandedId === run.id && tab === "plan" ? "primary" : "secondary"}
                      onClick={() => showPlan(run.id)}
                    >
                      Plan output
                    </Button>
                  </div>
                </div>
                {expandedId === run.id && tab === "plan" && (
                  <pre className={cx(
                    "max-h-72 overflow-auto rounded-md border p-3 font-mono text-xs leading-relaxed",
                    "border-slate-200 bg-white text-slate-700",
                    "dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300",
                  )}>
                    {planOutput}
                  </pre>
                )}
                <div className="flex gap-2 pt-1">
                  <Button
                    type="button"
                    data-testid="approve-button"
                    variant="primary"
                    className="bg-emerald-500 text-white hover:bg-emerald-400 active:bg-emerald-600 focus-visible:ring-emerald-400"
                    onClick={() => approve(run.id)}
                  >
                    ✓ Approve
                  </Button>
                  <Button
                    type="button"
                    data-testid="reject-button"
                    variant="danger"
                    onClick={() => reject(run.id)}
                  >
                    ✕ Reject
                  </Button>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
