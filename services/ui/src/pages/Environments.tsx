import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { extractError, listEnvLinks, pairSummaryText, type EnvLink } from "../api/envLinks";
import { AccountTag } from "../components/AccountTag";
import { Badge, Button, Card, EmptyState, SectionHeader, Skeleton } from "../components/ui";
import { useAccountColors } from "../hooks/useAccountColors";
import { useCurrentUser } from "../hooks/useAuth";
import { useBusinessUnitSelection } from "../hooks/useBusinessUnit";

export const ENV_BASE = "/governance/environments";

export function CompareIcon({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="18" cy="18" r="3" /><circle cx="6" cy="6" r="3" />
      <path d="M13 6h3a2 2 0 0 1 2 2v7" /><path d="M11 18H8a2 2 0 0 1-2-2V9" />
      <path d="m15 9-3-3 3-3" /><path d="m9 15 3 3-3 3" />
    </svg>
  );
}

/** "BU admin" in the UI mirrors the API's require_bu_admin (superadmin, for now). */
export function useIsBuAdmin(): boolean {
  const user = useCurrentUser();
  return !!user?.is_superadmin;
}

export function NodeSide({
  accountId,
  path,
  level,
}: {
  accountId: string | null;
  path: string;
  level: string;
}) {
  const { badgeFor } = useAccountColors();
  const badge = accountId ? badgeFor({ aws_account_id: accountId }) : null;
  const rest = path.split("/").slice(accountId ? 1 : 0).join("/");
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2">
        {badge ? (
          <AccountTag color={badge.color} name={badge.name} id={badge.id} provider="aws" />
        ) : accountId ? (
          <span className="font-mono text-[12px] text-brand-text">account-{accountId}</span>
        ) : null}
        {accountId && <span className="font-mono text-[11px] text-brand-muted">{accountId}</span>}
        <Badge tone="neutral">{level}</Badge>
      </div>
      {rest && (
        <p className="mt-0.5 truncate font-mono text-[11.5px] text-brand-muted" title={path}>
          {rest}
        </p>
      )}
    </div>
  );
}

export default function Environments() {
  const navigate = useNavigate();
  const isBuAdmin = useIsBuAdmin();
  const [buSlug] = useBusinessUnitSelection();
  const [links, setLinks] = useState<EnvLink[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLinks(await listEnvLinks());
      setError(null);
    } catch (e) {
      setError(extractError(e, "Failed to load environment links"));
      setLinks([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const canCreate = isBuAdmin && !!buSlug;
  const createTitle = !isBuAdmin
    ? "Only Business Unit admins can link environments"
    : !buSlug
      ? "Select a specific Business Unit first"
      : undefined;

  const createButton = (
    <span title={createTitle}>
      <Button disabled={!canCreate} onClick={() => navigate(`${ENV_BASE}/new`)}>
        Link environments
      </Button>
    </span>
  );

  return (
    <div>
      <SectionHeader
        eyebrow="GOVERNANCE"
        title="Environments"
        subtitle="Link a source to a target — whole accounts or single stacks — compare what differs, and promote changes through the normal plan → policy → approval pipeline."
        action={createButton}
      />

      {error && (
        <p className="mb-4 rounded-md border border-[rgba(var(--td-err-rgb),0.3)] bg-[rgba(var(--td-err-rgb),0.08)] px-3 py-2 text-[13px] text-[var(--td-err-ink)]">
          {error}
        </p>
      )}

      {links === null ? (
        <div className="space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
      ) : links.length === 0 ? (
        <EmptyState
          title="No linked environments yet"
          description="Link a Dev account to its Prod account (or one stack to its twin) to see the drift between them and promote changes across."
          icon={<CompareIcon />}
          action={createButton}
        />
      ) : (
        <ul className="space-y-3">
          {links.map((l) => (
            <li key={l.id}>
              <Card className="px-5 py-4">
                <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                  <div className="min-w-[180px]">
                    <Link
                      to={`${ENV_BASE}/${l.id}`}
                      className="font-display text-[16px] font-semibold text-brand-text hover:underline"
                    >
                      {l.name}
                    </Link>
                    <p className="mt-0.5 font-mono text-[11px] text-brand-muted">
                      {l.engine} · rules v{l.rules_version}
                    </p>
                  </div>
                  <div className="flex min-w-0 flex-1 items-center gap-3">
                    <NodeSide accountId={l.source_account_id} path={l.source_node.path} level={l.level} />
                    <span aria-label="to" className="font-mono text-brand-muted">→</span>
                    <NodeSide accountId={l.target_account_id} path={l.target_node.path} level={l.level} />
                  </div>
                  <div className="min-w-[200px] text-[12.5px] text-brand-textSoft">
                    <p>{pairSummaryText(l.pair_summary)}</p>
                    <p className="mt-0.5 font-mono text-[11px] text-brand-muted">
                      {l.last_promotion ? "last promotion —" : "no promotions yet"}
                    </p>
                  </div>
                  <Button variant="secondary" size="sm" onClick={() => navigate(`${ENV_BASE}/${l.id}`)}>
                    Compare
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
