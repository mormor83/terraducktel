import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import {
  createEnvLink,
  extractError,
  getEnvLink,
  previewPairs,
  updateEnvLink,
  type EnvNode,
  type EnvPair,
  type PairOverrides,
  type PairingPreview,
  type ProtectedRules,
  type RewriteRule,
} from "../api/envLinks";
import { EnvTreePicker } from "../components/env/EnvTreePicker";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, EmptyState, Input, Label, SectionHeader, Skeleton, cx } from "../components/ui";
import type { Workspace } from "../components/workspace-tree/types";
import { useAccountColors } from "../hooks/useAccountColors";
import { useBusinessUnitSelection } from "../hooks/useBusinessUnit";
import { CompareIcon, ENV_BASE, useIsBuAdmin } from "./Environments";

const EMPTY_OVERRIDES: PairOverrides = { pairs: [], exclude: [] };

const linesOf = (s: string) => s.split("\n").map((l) => l.trim()).filter(Boolean);

function PairList({
  title,
  tone,
  pairs,
  render,
  action,
}: {
  title: string;
  tone: "success" | "danger" | "violet" | "neutral";
  pairs: EnvPair[];
  render: (p: EnvPair) => string;
  action?: (p: EnvPair) => { label: string; onClick: () => void } | null;
}) {
  return (
    <div className="min-w-0">
      <div className="mb-2 flex items-center gap-2">
        <Badge tone={tone}>{pairs.length}</Badge>
        <span className="font-mono text-[10.5px] uppercase tracking-[1.2px] text-brand-muted">{title}</span>
      </div>
      <ul className="max-h-[260px] space-y-0.5 overflow-y-auto">
        {pairs.length === 0 && <li className="text-[12px] italic text-brand-muted">none</li>}
        {pairs.map((p, i) => {
          const a = action?.(p);
          return (
            <li key={`${p.source_rel}|${p.target_rel}|${i}`} className="group flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-brand-textSoft" title={render(p)}>
                {render(p)}
              </span>
              {a && (
                <button
                  type="button"
                  onClick={a.onClick}
                  className="shrink-0 font-mono text-[10.5px] text-brand-muted opacity-0 hover:text-brand-text group-hover:opacity-100 focus:opacity-100"
                >
                  {a.label}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function EnvLinkBuilder() {
  const { id } = useParams();
  const editing = !!id;
  const navigate = useNavigate();
  const isBuAdmin = useIsBuAdmin();
  const [buSlug] = useBusinessUnitSelection();
  const { badgeFor } = useAccountColors();
  const accountBadge = useCallback((acct: string) => badgeFor({ aws_account_id: acct }), [badgeFor]);

  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [name, setName] = useState("");
  const [source, setSource] = useState<EnvNode | null>(null);
  const [target, setTarget] = useState<EnvNode | null>(null);
  const [rules, setRules] = useState<RewriteRule[]>([]);
  const [overrides, setOverrides] = useState<PairOverrides>(EMPTY_OVERRIDES);
  const [keysText, setKeysText] = useState("");
  const [valuesText, setValuesText] = useState("");
  // For a new link, protected rules follow the preview's defaults until the
  // user edits them; after that their edits stick even if the nodes change.
  const [rulesTouched, setRulesTouched] = useState(editing);
  const [preview, setPreview] = useState<PairingPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [overrideDraft, setOverrideDraft] = useState({ source: "", target: "" });

  useEffect(() => {
    api
      .get<Workspace[]>("/v1/workspaces")
      .then((r) => setWorkspaces(r.data))
      .catch((e) => setLoadError(extractError(e, "Failed to load stacks")));
  }, []);

  useEffect(() => {
    if (!id) return;
    getEnvLink(id)
      .then((l) => {
        setName(l.name);
        setSource(l.source_node);
        setTarget(l.target_node);
        setRules(l.rewrite_rules);
        setOverrides({ pairs: l.pair_overrides.pairs ?? [], exclude: l.pair_overrides.exclude ?? [] });
        setKeysText((l.protected_rules.keys ?? []).join("\n"));
        setValuesText((l.protected_rules.values ?? []).join("\n"));
      })
      .catch((e) => setLoadError(extractError(e, "Failed to load link")));
  }, [id]);

  // Live pairing preview, debounced; stale responses are dropped.
  const seq = useRef(0);
  useEffect(() => {
    if (!source || !target) {
      setPreview(null);
      return;
    }
    const mine = ++seq.current;
    setPreviewing(true);
    const t = window.setTimeout(async () => {
      try {
        const p = await previewPairs({
          source_node: source,
          target_node: target,
          rewrite_rules: rules.filter((r) => r.from),
          pair_overrides: overrides,
        });
        if (mine !== seq.current) return;
        setPreview(p);
        setPreviewError(null);
        if (!rulesTouched) {
          setKeysText(p.default_protected_rules.keys.join("\n"));
          setValuesText(p.default_protected_rules.values.join("\n"));
        }
      } catch (e) {
        if (mine !== seq.current) return;
        setPreview(null);
        setPreviewError(extractError(e, "Pairing preview failed"));
      } finally {
        if (mine === seq.current) setPreviewing(false);
      }
    }, 350);
    return () => window.clearTimeout(t);
  }, [source, target, rules, overrides, rulesTouched]);

  const buckets = useMemo(() => {
    const p = preview?.pairs ?? [];
    return {
      matched: p.filter((x) => x.source_rel !== null && x.target_rel !== null && x.status !== "excluded"),
      onlySource: p.filter((x) => x.status === "missing_in_target"),
      onlyTarget: p.filter((x) => x.status === "missing_in_source"),
      excluded: p.filter((x) => x.status === "excluded"),
    };
  }, [preview]);

  const exclude = (side: "source" | "target", path: string) =>
    setOverrides((o) => ({ ...o, exclude: [...o.exclude, { side, path }] }));
  const unexclude = (p: EnvPair) =>
    setOverrides((o) => ({
      ...o,
      exclude: o.exclude.filter(
        (e) => !((e.side === "source" && e.path === p.source_rel) || (e.side === "target" && e.path === p.target_rel)),
      ),
    }));

  const swap = () => {
    setSource(target);
    setTarget(source);
  };

  const protectedRules: ProtectedRules = { keys: linesOf(keysText), values: linesOf(valuesText) };
  const canSave = isBuAdmin && !!buSlug && !!name.trim() && !!source && !!target && !!preview && !saving;

  const save = async () => {
    if (!source || !target) return;
    setSaving(true);
    setSaveError(null);
    const body = {
      name: name.trim(),
      source_node: source,
      target_node: target,
      rewrite_rules: rules.filter((r) => r.from),
      pair_overrides: overrides,
      protected_rules: protectedRules,
    };
    try {
      const saved = editing ? await updateEnvLink(id!, body) : await createEnvLink(body);
      navigate(`${ENV_BASE}/${saved.id}`);
    } catch (e) {
      setSaveError(extractError(e, "Failed to save link"));
    } finally {
      setSaving(false);
    }
  };

  if (!isBuAdmin) {
    return (
      <div>
        <SectionHeader eyebrow="GOVERNANCE · ENVIRONMENTS" title={editing ? "Edit link" : "Link environments"} />
        <EmptyState title="Business Unit admins only" description="Ask a Business Unit admin to create or edit environment links." icon={<CompareIcon />} />
      </div>
    );
  }
  if (!buSlug) {
    return (
      <div>
        <SectionHeader eyebrow="GOVERNANCE · ENVIRONMENTS" title={editing ? "Edit link" : "Link environments"} />
        <EmptyState title="Select a Business Unit" description="Environment links belong to one Business Unit. Pick a specific BU in the top bar." icon={<CompareIcon />} />
      </div>
    );
  }

  return (
    <div>
      <SectionHeader
        eyebrow="GOVERNANCE · ENVIRONMENTS"
        title={editing ? "Edit link" : "Link environments"}
        subtitle="Pick one node on each side at the same level. Stacks under both are paired by their path below the node; rewrite rules and overrides handle the rest."
      />

      {loadError && <p className="mb-4 text-[13px] text-[var(--td-err-ink)]">{loadError}</p>}

      <div className="mb-5 max-w-md">
        <Label htmlFor="env-link-name">Name</Label>
        <Input id="env-link-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="dev-to-prod" maxLength={120} />
      </div>

      {workspaces === null ? (
        <Skeleton className="h-[420px]" />
      ) : (
        <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-[1fr_auto_1fr]">
          <EnvTreePicker
            label="Source"
            workspaces={workspaces}
            accountBadge={accountBadge}
            value={source}
            onChange={setSource}
            lockLevel={target?.level}
            disabledPath={target?.path}
          />
          <div className="flex justify-center lg:pt-24">
            <Button variant="secondary" size="sm" onClick={swap} disabled={!source && !target} title="Swap source and target">
              ⇄
            </Button>
          </div>
          <EnvTreePicker
            label="Target"
            workspaces={workspaces}
            accountBadge={accountBadge}
            value={target}
            onChange={setTarget}
            lockLevel={source?.level}
            disabledPath={source?.path}
          />
        </div>
      )}

      <div className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Rewrite rules</CardTitle></CardHeader>
          <CardBody>
            <p className="mb-3 text-[12.5px] text-brand-muted">
              Applied in order to each source path before matching — e.g. <span className="font-mono">us-east-1 → eu-west-1</span>.
            </p>
            <ul className="space-y-2">
              {rules.map((r, i) => (
                <li key={i} className="flex items-center gap-2">
                  <Input aria-label={`Rule ${i + 1} from`} value={r.from} placeholder="from"
                    onChange={(e) => setRules(rules.map((x, j) => (j === i ? { ...x, from: e.target.value } : x)))} className="font-mono" />
                  <span className="font-mono text-brand-muted">→</span>
                  <Input aria-label={`Rule ${i + 1} to`} value={r.to} placeholder="to"
                    onChange={(e) => setRules(rules.map((x, j) => (j === i ? { ...x, to: e.target.value } : x)))} className="font-mono" />
                  <label className="flex shrink-0 items-center gap-1 font-mono text-[11px] text-brand-muted">
                    <input type="checkbox" checked={r.regex}
                      onChange={(e) => setRules(rules.map((x, j) => (j === i ? { ...x, regex: e.target.checked } : x)))} />
                    regex
                  </label>
                  <Button variant="ghost" size="sm" onClick={() => setRules(rules.filter((_, j) => j !== i))} aria-label={`Remove rule ${i + 1}`}>✕</Button>
                </li>
              ))}
            </ul>
            <Button variant="secondary" size="sm" className="mt-3" disabled={rules.length >= 20}
              onClick={() => setRules([...rules, { from: "", to: "", regex: false }])}>
              Add rule
            </Button>

            <div className="mt-5 border-t border-brand-border pt-4">
              <p className="mb-2 font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">Explicit pairs</p>
              <ul className="mb-2 space-y-1">
                {overrides.pairs.map((p, i) => (
                  <li key={i} className="flex items-center gap-2 font-mono text-[11.5px] text-brand-textSoft">
                    <span className="truncate">{p.source || "."}</span>
                    <span className="text-brand-muted">→</span>
                    <span className="truncate">{p.target || "."}</span>
                    <Button variant="ghost" size="sm" aria-label="Remove pair"
                      onClick={() => setOverrides({ ...overrides, pairs: overrides.pairs.filter((_, j) => j !== i) })}>✕</Button>
                  </li>
                ))}
              </ul>
              <div className="flex items-center gap-2">
                <select aria-label="Explicit pair source" value={overrideDraft.source}
                  onChange={(e) => setOverrideDraft({ ...overrideDraft, source: e.target.value })}
                  className="h-[30px] min-w-0 flex-1 rounded-md border border-brand-border bg-brand-surface px-2 font-mono text-[11.5px] text-brand-text">
                  <option value="">source…</option>
                  {buckets.onlySource.map((p) => <option key={p.source_rel!} value={p.source_rel!}>{p.source_rel}</option>)}
                </select>
                <span className="font-mono text-brand-muted">→</span>
                <select aria-label="Explicit pair target" value={overrideDraft.target}
                  onChange={(e) => setOverrideDraft({ ...overrideDraft, target: e.target.value })}
                  className="h-[30px] min-w-0 flex-1 rounded-md border border-brand-border bg-brand-surface px-2 font-mono text-[11.5px] text-brand-text">
                  <option value="">target…</option>
                  {buckets.onlyTarget.map((p) => <option key={p.target_rel!} value={p.target_rel!}>{p.target_rel}</option>)}
                </select>
                <Button variant="secondary" size="sm" disabled={!overrideDraft.source || !overrideDraft.target}
                  onClick={() => {
                    setOverrides({ ...overrides, pairs: [...overrides.pairs, overrideDraft] });
                    setOverrideDraft({ source: "", target: "" });
                  }}>
                  Pair
                </Button>
              </div>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader><CardTitle>Protected values</CardTitle></CardHeader>
          <CardBody>
            <p className="mb-3 text-[12.5px] text-brand-muted">
              Environment-specific keys and values. They show in the diff but are left out of promotion unless explicitly toggled with a reason. Backend / remote-state config is never promotable. One glob per line.
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="env-protected-keys">Keys</Label>
                <textarea id="env-protected-keys" value={keysText} rows={9}
                  onChange={(e) => { setKeysText(e.target.value); setRulesTouched(true); }}
                  className="w-full rounded-md border border-brand-border bg-brand-surface p-2 font-mono text-[11.5px] text-brand-text" />
              </div>
              <div>
                <Label htmlFor="env-protected-values">Values</Label>
                <textarea id="env-protected-values" value={valuesText} rows={9}
                  onChange={(e) => { setValuesText(e.target.value); setRulesTouched(true); }}
                  className="w-full rounded-md border border-brand-border bg-brand-surface p-2 font-mono text-[11.5px] text-brand-text" />
              </div>
            </div>
          </CardBody>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Pairing preview</CardTitle>
          {previewing && <span className="font-mono text-[11px] text-brand-muted">updating…</span>}
        </CardHeader>
        <CardBody>
          {!source || !target ? (
            <p className="text-[13px] text-brand-muted">Pick a source and a target to see how their stacks pair.</p>
          ) : previewError ? (
            <p className="text-[13px] text-[var(--td-err-ink)]">{previewError}</p>
          ) : !preview ? (
            <Skeleton className="h-24" />
          ) : (
            <>
              {preview.warnings.length > 0 && (
                <ul className="mb-3 space-y-1">
                  {preview.warnings.map((w) => (
                    <li key={w} className="text-[12.5px] text-[var(--td-warn-ink)]">⚠ {w}</li>
                  ))}
                </ul>
              )}
              {preview.helm_skipped > 0 && (
                <p className="mb-3 text-[12px] text-brand-muted">
                  {preview.helm_skipped} Helm stack{preview.helm_skipped === 1 ? "" : "s"} skipped — Helm environments are coming soon.
                </p>
              )}
              <div className={cx("grid grid-cols-1 gap-4 md:grid-cols-3", buckets.excluded.length > 0 && "xl:grid-cols-4")}>
                <PairList title="Matched" tone="success" pairs={buckets.matched}
                  render={(p) => (p.source_rel === p.target_rel ? p.target_rel || "." : `${p.source_rel || "."} → ${p.target_rel || "."}`)}
                  action={(p) => (p.reason === "override" ? null : { label: "exclude", onClick: () => exclude("source", p.source_rel!) })} />
                <PairList title="Only in source" tone="danger" pairs={buckets.onlySource}
                  render={(p) => p.source_rel || "."}
                  action={(p) => ({ label: "exclude", onClick: () => exclude("source", p.source_rel!) })} />
                <PairList title="Only in target" tone="violet" pairs={buckets.onlyTarget}
                  render={(p) => p.target_rel || "."}
                  action={(p) => ({ label: "exclude", onClick: () => exclude("target", p.target_rel!) })} />
                {buckets.excluded.length > 0 && (
                  <PairList title="Excluded" tone="neutral" pairs={buckets.excluded}
                    render={(p) => `${p.source_rel ?? p.target_rel ?? "."}${p.reason ? ` (${p.reason})` : ""}`}
                    action={(p) => (p.reason?.startsWith("excluded") ? { label: "include", onClick: () => unexclude(p) } : null)} />
                )}
              </div>
            </>
          )}
        </CardBody>
      </Card>

      <div className="mt-5 flex items-center justify-end gap-3">
        {saveError && <p className="mr-auto text-[13px] text-[var(--td-err-ink)]">{saveError}</p>}
        <Button variant="ghost" onClick={() => navigate(editing ? `${ENV_BASE}/${id}` : ENV_BASE)}>Cancel</Button>
        <Button disabled={!canSave} onClick={save}>{saving ? "Saving…" : editing ? "Save link" : "Create link"}</Button>
      </div>
    </div>
  );
}
