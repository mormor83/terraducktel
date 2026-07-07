import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useCurrentUser, hasMinRole } from "../hooks/useAuth";
import { Badge, Button, Card, CardBody, EmptyState, Skeleton } from "../components/ui";

type IgnoreRule = { id: string; match_type: string; pattern: string; note: string; created_at: string | null };
const MANAGED: ReadonlySet<string> = new Set(["codified", "drifted", "ghost"]);

type IacStatus =
  | "codified" | "drifted" | "ghost" | "unmanaged" | "service_managed" | "ignored" | "undetermined";

type Facets = {
  providers: string[];
  regions: string[];
  accounts: string[];
  asset_types: string[];
};

type Summary = {
  total: number;
  codification_pct: number;
  counts: Record<IacStatus, number>;
  facets: Facets;
};

type Asset = {
  asset_id: string;
  address: string;
  asset_type: string;
  provider: string;
  region: string;
  account_id: string;
  iac_status: IacStatus;
  drift_summary: string;
  workspace_id: string | null;
  last_seen: string | null;
};

type BadgeTone = "success" | "amber" | "danger" | "violet" | "neutral" | "info";

/** Per-state theme: badge tone + a literal color ramp for dots/rails/tints.
 *  Colors are default-Tailwind palettes already used by the Badge component. */
const STATUS_META: Record<
  IacStatus,
  { label: string; tone: BadgeTone; hint: string; dot: string; rail: string; tint: string; ink: string }
> = {
  codified: {
    label: "Codified", tone: "success", hint: "managed & in sync",
    dot: "bg-accent-500", rail: "bg-accent-400",
    tint: "from-accent-50 dark:from-accent-500/10", ink: "text-accent-700 dark:text-accent-300",
  },
  drifted: {
    label: "Drifted", tone: "amber", hint: "deviated from IaC",
    dot: "bg-amber-500", rail: "bg-amber-400",
    tint: "from-amber-50 dark:from-amber-500/10", ink: "text-amber-700 dark:text-amber-300",
  },
  unmanaged: {
    label: "Unmanaged", tone: "violet", hint: "not in any IaC",
    dot: "bg-violet-500", rail: "bg-violet-400",
    tint: "from-violet-50 dark:from-violet-500/10", ink: "text-violet-700 dark:text-violet-300",
  },
  service_managed: {
    label: "Service-managed", tone: "info", hint: "owned by an AWS service",
    dot: "bg-sky-500", rail: "bg-sky-400",
    tint: "from-sky-50 dark:from-sky-500/10", ink: "text-sky-700 dark:text-sky-300",
  },
  ghost: {
    label: "Ghost", tone: "danger", hint: "in code, gone from cloud",
    dot: "bg-red-500", rail: "bg-red-400",
    tint: "from-red-50 dark:from-red-500/10", ink: "text-red-700 dark:text-red-300",
  },
  ignored: {
    label: "Ignored", tone: "neutral", hint: "excluded",
    dot: "bg-slate-400", rail: "bg-slate-300",
    tint: "from-slate-50 dark:from-slate-500/10", ink: "text-slate-600 dark:text-slate-300",
  },
  undetermined: {
    label: "Undetermined", tone: "info", hint: "unclassified",
    dot: "bg-brand-400", rail: "bg-brand-300",
    tint: "from-brand-50 dark:from-brand-500/10", ink: "text-brand-700 dark:text-brand-200",
  },
};

const CARD_STATES: IacStatus[] = ["codified", "drifted", "unmanaged", "service_managed", "ghost"];

export default function Inventory() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Honor ?status=… from the URL so the Dashboard "Drifted" tile lands filtered.
  const [status, setStatus] = useState(
    () => new URLSearchParams(window.location.search).get("status") || "",
  );
  const [provider, setProvider] = useState("");
  const [region, setRegion] = useState("");
  const [account, setAccount] = useState("");
  const [search, setSearch] = useState("");

  async function loadSummary() {
    // Scope the KPI cards to the same provider/region/account/search the table
    // uses (status is omitted — the cards are the status breakdown).
    const params: Record<string, string> = {};
    if (provider) params.provider = provider;
    if (region) params.region = region;
    if (account) params.account_id = account;
    if (search) params.search = search;
    try {
      const r = await api.get("/v1/inventory/summary", { params });
      setSummary(r.data);
      setErr(null);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to load");
    }
  }

  async function loadAssets() {
    const params: Record<string, string> = {};
    if (status) params.iac_status = status;
    if (provider) params.provider = provider;
    if (region) params.region = region;
    if (account) params.account_id = account;
    if (search) params.search = search;
    try {
      const r = await api.get("/v1/inventory/assets", { params });
      setAssets(r.data.items ?? []);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to load assets");
    }
  }

  useEffect(() => {
    setLoading(true);
    Promise.all([loadSummary(), loadAssets()]).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Re-scope both the KPI cards and the table when filters change. (The
    // summary endpoint ignores status, so the cards stay the full breakdown.)
    loadSummary();
    loadAssets();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, provider, region, account, search]);

  const facets = summary?.facets;
  const codification = summary?.codification_pct ?? 0;
  const filtersActive = useMemo(
    () => Boolean(status || provider || region || account || search),
    [status, provider, region, account, search],
  );

  function clearFilters() {
    setStatus(""); setProvider(""); setRegion(""); setAccount(""); setSearch("");
  }

  // ─── ignore rules (admin) ──────────────────────────────────────────────
  const user = useCurrentUser();
  const isAdmin = hasMinRole(user, "admin");
  const [rules, setRules] = useState<IgnoreRule[]>([]);
  const [showRules, setShowRules] = useState(false);
  const [ruleType, setRuleType] = useState<"arn_glob" | "asset_type">("arn_glob");
  const [rulePattern, setRulePattern] = useState("");
  const [ruleNote, setRuleNote] = useState("");
  const [ruleBusy, setRuleBusy] = useState(false);

  async function loadRules() {
    try {
      const r = await api.get("/v1/inventory/ignore-rules");
      setRules(r.data ?? []);
    } catch { /* non-fatal */ }
  }
  useEffect(() => {
    if (isAdmin) loadRules();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  async function addRule(match_type: string, pattern: string, note = "") {
    setRuleBusy(true);
    try {
      await api.post("/v1/inventory/ignore-rules", { match_type, pattern, note });
      await Promise.all([loadRules(), loadSummary(), loadAssets()]);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to add ignore rule");
    } finally {
      setRuleBusy(false);
    }
  }
  async function submitRule() {
    if (!rulePattern.trim()) return;
    await addRule(ruleType, rulePattern.trim(), ruleNote.trim());
    setRulePattern(""); setRuleNote("");
  }
  async function deleteRule(id: string) {
    try {
      await api.delete(`/v1/inventory/ignore-rules/${id}`);
      await Promise.all([loadRules(), loadSummary(), loadAssets()]);
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? "Failed to delete rule");
    }
  }
  // Quick-ignore a specific asset → an arn_glob rule matching its exact ARN.
  async function ignoreAsset(arn: string) {
    await addRule("arn_glob", arn, "ignored from inventory");
  }

  return (
    <div className="relative">
      {/* Atmospheric brand wash behind the header */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-8 h-64 bg-gradient-to-b from-brand-100/50 via-brand-50/20 to-transparent blur-2xl dark:from-brand-500/10 dark:via-brand-500/5"
      />

      <div className="relative">
        {/* Header */}
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-3xl font-semibold tracking-tight text-brand-text dark:text-slate-100">
              Cloud inventory
            </h1>
            <p className="mt-1.5 max-w-xl text-sm text-brand-muted dark:text-slate-400">
              Every discovered cloud asset, classified by its Infrastructure-as-Code status — scoped to the current business unit.
            </p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => { loadSummary(); loadAssets(); }}
          >
            ↻ Refresh
          </Button>
        </div>

        {err && (
          <Card className="mb-4 border-red-300 bg-red-50 dark:border-red-900/50 dark:bg-red-950/30">
            <CardBody className="text-sm text-red-700 dark:text-red-300">⚠ {err}</CardBody>
          </Card>
        )}

        {/* Hero: codification gauge + state tiles */}
        <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(260px,340px)_1fr]">
          <CodificationGauge pct={codification} total={summary?.total ?? 0} loading={loading} />

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
            {CARD_STATES.map((s, i) => (
              <StateTile
                key={s}
                state={s}
                value={summary?.counts?.[s] ?? 0}
                loading={loading}
                active={status === s}
                delay={i * 60}
                onClick={() => setStatus(status === s ? "" : s)}
              />
            ))}
          </div>
        </div>

        {/* Filter toolbar */}
        <Card className="mb-4 shadow-td-sm">
          <CardBody className="flex flex-wrap items-center gap-2.5">
            <span className="text-xs font-medium uppercase tracking-wider text-brand-muted dark:text-slate-500">
              Filter
            </span>
            <FilterSelect label="Status" value={status} onChange={setStatus}
              options={Object.keys(STATUS_META).map((k) => ({ value: k, label: STATUS_META[k as IacStatus].label }))} />
            <FilterSelect label="Provider" value={provider} onChange={setProvider}
              options={(facets?.providers ?? []).map((v) => ({ value: v, label: v }))} />
            <FilterSelect label="Region" value={region} onChange={setRegion}
              options={(facets?.regions ?? []).map((v) => ({ value: v, label: v }))} />
            <FilterSelect label="Account" value={account} onChange={setAccount}
              options={(facets?.accounts ?? []).map((v) => ({ value: v, label: v }))} />
            <div className="relative flex-1 min-w-[200px]">
              <svg className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-brand-muted dark:text-slate-500"
                width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
              </svg>
              <input
                className="h-9 w-full rounded-md border border-brand-border bg-white pl-9 pr-3 text-sm text-brand-text placeholder:text-brand-muted focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-400/30 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-100 dark:placeholder:text-slate-500"
                placeholder="Search address or ARN…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            {filtersActive && (
              <Button variant="ghost" size="sm" onClick={clearFilters}>Clear</Button>
            )}
          </CardBody>
        </Card>

        {/* Ignore rules (admin) — suppress inventory noise the tag-based
            service-managed detection misses. Matching non-managed assets become
            "ignored" (out of Unmanaged + the codification base). */}
        {isAdmin && (
          <Card className="mb-4 shadow-td-sm">
            <CardBody>
              <button
                type="button"
                onClick={() => setShowRules((v) => !v)}
                className="flex w-full items-center justify-between text-left"
              >
                <span className="flex items-center gap-2 text-sm font-medium text-brand-text dark:text-slate-200">
                  <span className="text-brand-muted">{showRules ? "▾" : "▸"}</span>
                  Ignore rules
                  {rules.length > 0 && <Badge tone="neutral">{rules.length}</Badge>}
                </span>
                <span className="text-xs text-brand-muted">suppress inventory noise</span>
              </button>

              {showRules && (
                <div className="mt-3 space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <select
                      aria-label="Match type"
                      value={ruleType}
                      onChange={(e) => setRuleType(e.target.value as "arn_glob" | "asset_type")}
                      className="h-9 rounded-md border border-brand-border bg-white px-2 text-sm text-brand-text dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-100"
                    >
                      <option value="arn_glob">ARN glob</option>
                      <option value="asset_type">Asset type</option>
                    </select>
                    <input
                      value={rulePattern}
                      onChange={(e) => setRulePattern(e.target.value)}
                      placeholder={ruleType === "arn_glob" ? "arn:aws:cloudformation:*:*:stack/StackSet-*" : "aws_cloudwatch_log_group"}
                      className="h-9 flex-1 min-w-[240px] rounded-md border border-brand-border bg-white px-3 font-mono text-xs text-brand-text placeholder:text-brand-muted focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-400/30 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-100"
                    />
                    <input
                      value={ruleNote}
                      onChange={(e) => setRuleNote(e.target.value)}
                      placeholder="note (optional)"
                      className="h-9 min-w-[140px] rounded-md border border-brand-border bg-white px-3 text-sm text-brand-text placeholder:text-brand-muted focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-400/30 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-100"
                    />
                    <Button size="sm" onClick={submitRule} disabled={ruleBusy || !rulePattern.trim()}>
                      Add rule
                    </Button>
                  </div>
                  {rules.length === 0 ? (
                    <p className="text-xs text-brand-muted">
                      No ignore rules. Service-owned resources (EKS/CloudFormation/Karpenter) are auto-classified; add a rule for anything else you want suppressed.
                    </p>
                  ) : (
                    <ul className="divide-y divide-brand-border/70">
                      {rules.map((r) => (
                        <li key={r.id} className="flex items-center gap-3 py-2 text-sm">
                          <Badge tone="neutral">{r.match_type === "arn_glob" ? "ARN" : "type"}</Badge>
                          <span className="flex-1 truncate font-mono text-xs text-brand-text dark:text-slate-200">{r.pattern}</span>
                          {r.note && <span className="hidden truncate text-[11px] text-brand-muted sm:inline">{r.note}</span>}
                          <Button variant="ghost" size="sm" onClick={() => deleteRule(r.id)}>Remove</Button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </CardBody>
          </Card>
        )}

        {/* Asset table */}
        {loading ? (
          <Card>
            <CardBody className="space-y-3">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-4 w-1/3" />
            </CardBody>
          </Card>
        ) : assets.length === 0 ? (
          <EmptyState
            title={filtersActive ? "No assets match these filters" : "No assets discovered yet"}
            description={
              filtersActive
                ? "Try clearing or broadening the filters."
                : "The drift-detector populates the inventory on its next scan."
            }
            action={filtersActive ? <Button variant="secondary" size="sm" onClick={clearFilters}>Clear filters</Button> : undefined}
          />
        ) : (
          <Card className="overflow-hidden shadow-td-md">
            <div className="flex items-center justify-between border-b border-brand-border px-5 py-3 dark:border-slate-800/80">
              <h3 className="font-display text-sm font-semibold text-brand-text dark:text-slate-200">Assets</h3>
              <span className="text-xs text-brand-muted dark:text-slate-500">
                {assets.length} shown{filtersActive ? " (filtered)" : ""}
              </span>
            </div>
            <div className="overflow-x-auto">
              {/* table-fixed + percentage widths so columns scale to the
                  viewport instead of growing to the (very long) mono asset
                  addresses and forcing horizontal scroll. Long values truncate
                  with a hover tooltip. */}
              <table className="w-full table-fixed text-sm">
                <thead>
                  <tr className="border-b border-brand-border text-left text-[11px] uppercase tracking-wider text-brand-muted dark:border-slate-800/80 dark:text-slate-500">
                    <th className="w-[48%] px-5 py-2.5 font-semibold">Asset</th>
                    <th className="w-[17%] px-4 py-2.5 font-semibold">Type</th>
                    <th className="w-[12%] px-4 py-2.5 font-semibold">Status</th>
                    <th className="w-[10%] px-4 py-2.5 font-semibold">Region</th>
                    <th className="w-[13%] px-4 py-2.5 font-semibold">Account</th>
                    {isAdmin && <th className="w-[64px] px-4 py-2.5" />}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-border/70 dark:divide-slate-800/60">
                  {assets.map((a) => {
                    const meta = STATUS_META[a.iac_status] ?? STATUS_META.undetermined;
                    return (
                      <tr key={a.asset_id} className="group transition-colors hover:bg-brand-surface2/70 dark:hover:bg-slate-800/30">
                        <td className="relative py-3 pl-5 pr-4 align-top">
                          <span className={`absolute left-0 top-3 h-7 w-1 rounded-r ${meta.rail}`} aria-hidden />
                          <div className="min-w-0">
                            {/* Wrap (don't truncate) — the distinguishing part
                                of an ARN/address is its tail, so truncating
                                hides what matters. break-all wraps the long
                                no-space mono strings within the fixed column. */}
                            <span className="block break-all font-mono text-xs text-brand-text dark:text-slate-200">
                              {a.address || a.asset_id}
                            </span>
                            {a.address && (
                              <span
                                className="hidden truncate font-mono text-[11px] text-brand-muted lg:block dark:text-slate-500"
                                title={a.asset_id}
                              >
                                {a.asset_id}
                              </span>
                            )}
                            {a.drift_summary && (
                              <div className="mt-0.5 truncate text-[11px] text-brand-muted dark:text-slate-500" title={a.drift_summary}>
                                {a.drift_summary}
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3 align-top text-brand-textSoft dark:text-slate-400">
                          <span className="block break-all font-mono text-xs">
                            {a.asset_type || "—"}
                          </span>
                        </td>
                        <td className="px-4 py-3 align-top">
                          <span className="inline-flex items-center gap-1.5">
                            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${meta.dot}`} aria-hidden />
                            <Badge tone={meta.tone}>{meta.label}</Badge>
                          </span>
                        </td>
                        <td className="px-4 py-3 align-top font-mono text-xs text-brand-muted dark:text-slate-500">
                          <span className="block truncate" title={a.region || undefined}>{a.region || "—"}</span>
                        </td>
                        <td className="px-4 py-3 align-top font-mono text-xs text-brand-muted dark:text-slate-500">
                          <span className="block truncate" title={a.account_id || undefined}>{a.account_id || "—"}</span>
                        </td>
                        {isAdmin && (
                          <td className="px-4 py-3 text-right align-top">
                            {!MANAGED.has(a.iac_status) && a.iac_status !== "ignored" && (
                              <button
                                type="button"
                                onClick={() => ignoreAsset(a.asset_id)}
                                title="Add an ignore rule for this resource"
                                className="text-xs text-brand-600 opacity-0 transition-opacity hover:underline group-hover:opacity-100 dark:text-brand-300"
                              >
                                Ignore
                              </button>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

/** Donut gauge for the codification %, the page's hero metric. */
function CodificationGauge({ pct, total, loading }: { pct: number; total: number; loading: boolean }) {
  const R = 52;
  const C = 2 * Math.PI * R;
  const [shown, setShown] = useState(0);
  useEffect(() => {
    // Animate the ring + number up to the real value after mount.
    if (loading) return;
    const id = requestAnimationFrame(() => setShown(pct));
    return () => cancelAnimationFrame(id);
  }, [pct, loading]);

  const offset = C * (1 - shown / 100);
  const ring = pct >= 80 ? "text-accent-500" : pct >= 50 ? "text-amber-500" : "text-red-500";

  return (
    <Card className="tdt-fade-up overflow-hidden shadow-td-md">
      <CardBody className="flex h-full flex-col items-center justify-center gap-3 py-7">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-muted dark:text-slate-500">
          Codification
        </p>
        <div className="relative grid place-items-center">
          <svg width="148" height="148" viewBox="0 0 148 148" className="-rotate-90">
            <circle cx="74" cy="74" r={R} fill="none" strokeWidth="11"
              className="stroke-brand-100 dark:stroke-slate-800" />
            <circle
              cx="74" cy="74" r={R} fill="none" strokeWidth="11" strokeLinecap="round"
              className={`${ring} transition-[stroke-dashoffset] duration-[1100ms] ease-out`}
              stroke="currentColor"
              strokeDasharray={C}
              strokeDashoffset={loading ? C : offset}
            />
          </svg>
          <div className="absolute flex flex-col items-center">
            <span className="font-display text-4xl font-semibold tracking-tight text-brand-text dark:text-slate-100">
              {loading ? "—" : `${pct}%`}
            </span>
            <span className="text-[11px] text-brand-muted dark:text-slate-500">under IaC</span>
          </div>
        </div>
        <p className="text-xs text-brand-muted dark:text-slate-500">
          {loading ? "scanning…" : `${total} assets discovered`}
        </p>
      </CardBody>
    </Card>
  );
}

/** Clickable KPI tile for one IaC state; doubles as a status filter. */
function StateTile({
  state, value, loading, active, delay, onClick,
}: {
  state: IacStatus; value: number; loading: boolean; active: boolean; delay: number; onClick: () => void;
}) {
  const meta = STATUS_META[state];
  return (
    <button
      type="button"
      onClick={onClick}
      style={{ animationDelay: `${delay}ms` }}
      aria-pressed={active}
      className={[
        "tdt-fade-up group relative overflow-hidden rounded-lg border bg-gradient-to-br to-transparent p-4 text-left transition-all",
        meta.tint,
        "shadow-td-sm hover:-translate-y-0.5 hover:shadow-td-md",
        active
          ? "border-brand-400 ring-2 ring-brand-400/40 dark:border-brand-400"
          : "border-brand-border dark:border-slate-800/80",
      ].join(" ")}
    >
      <span className={`absolute inset-x-0 top-0 h-[3px] ${meta.rail}`} aria-hidden />
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${meta.dot}`} aria-hidden />
        <span className="text-xs font-medium uppercase tracking-wider text-brand-textSoft dark:text-slate-400">
          {meta.label}
        </span>
      </div>
      <div className={`mt-2 font-display text-3xl font-semibold tabular-nums ${value ? meta.ink : "text-brand-muted dark:text-slate-600"}`}>
        {loading ? "—" : value}
      </div>
      <div className="mt-0.5 text-[11px] text-brand-muted dark:text-slate-500">{meta.hint}</div>
    </button>
  );
}

function FilterSelect({
  label, value, onChange, options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="relative">
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={[
          "h-9 appearance-none rounded-md border bg-white pl-3 pr-8 text-sm text-brand-text focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-400/30 dark:bg-slate-900/60 dark:text-slate-100",
          value ? "border-brand-400 dark:border-brand-500/70" : "border-brand-border dark:border-slate-700",
        ].join(" ")}
      >
        <option value="">{label}: all</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      <svg className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-brand-muted dark:text-slate-500"
        width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
        <path d="m6 9 6 6 6-6" />
      </svg>
    </div>
  );
}
