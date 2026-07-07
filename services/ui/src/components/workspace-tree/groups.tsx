// Grouping components for the workspace tree, inner → outer:
// FolderTreeBody → FolderGroup → RegionGroup → CloudGroupCard, with thin
// AccountGroup (AWS) / AzureSubscriptionGroup (Azure) wrappers over the card.

import { ReactNode, useEffect, useMemo, useState } from "react";
import { Badge, Card } from "../ui";
import { AzureIcon, CloudIcon, FolderIcon } from "./icons";
import {
  buildFolderTree,
  collectNodeWorkspaces,
  countNodeWorkspaces,
  type FolderNode,
} from "./paths";
import { BulkWebhookButton, TreeRow } from "./primitives";
import { WorkspaceLeafRow } from "./WorkspaceLeafRow";
import type {
  AwsAccountLite,
  AzureSubscriptionLite,
  ExpandSignal,
  Run,
  Workspace,
} from "./types";

function FolderTreeBody({
  node,
  depth,
  latestByWs,
  onChanged,
  expandSignal,
  awsAccounts,
  azureSubscriptions,
}: {
  node: FolderNode;
  depth: number;
  latestByWs: Map<string, Run>;
  onChanged: () => void;
  expandSignal: ExpandSignal;
  awsAccounts: AwsAccountLite[];
  azureSubscriptions: AzureSubscriptionLite[];
}) {
  const folderNames = [...node.folders.keys()].sort();
  const wsSorted = [...node.workspaces].sort((a, b) => a.leaf.localeCompare(b.leaf));
  return (
    <div>
      {folderNames.map((k) => (
        <FolderGroup
          key={`f:${k}`}
          folder={node.folders.get(k) as FolderNode}
          depth={depth}
          latestByWs={latestByWs}
          onChanged={onChanged}
          expandSignal={expandSignal}
          awsAccounts={awsAccounts}
          azureSubscriptions={azureSubscriptions}
        />
      ))}
      {wsSorted.map(({ ws, leaf }) => (
        <WorkspaceLeafRow
          key={ws.id}
          workspace={ws}
          displayName={leaf}
          depth={depth}
          recentRun={latestByWs.get(ws.id)}
          onChanged={onChanged}
          awsAccounts={awsAccounts}
          azureSubscriptions={azureSubscriptions}
        />
      ))}
    </div>
  );
}

function FolderGroup({
  folder,
  depth,
  latestByWs,
  onChanged,
  expandSignal,
  awsAccounts,
  azureSubscriptions,
}: {
  folder: FolderNode;
  depth: number;
  latestByWs: Map<string, Run>;
  onChanged: () => void;
  expandSignal: ExpandSignal;
  awsAccounts: AwsAccountLite[];
  azureSubscriptions: AzureSubscriptionLite[];
}) {
  // Folders start collapsed so the dashboard isn't a wall of nested rows on
  // load — operators expand the levels they care about. The chevron state is
  // local to each folder so expanding one doesn't unfurl siblings.
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (expandSignal) setOpen(expandSignal.expand);
  }, [expandSignal?.version]);
  const count = countNodeWorkspaces(folder);
  return (
    <div>
      <TreeRow
        depth={depth}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        icon={<FolderIcon open={open} />}
        label={<span className="font-mono">{folder.name}</span>}
        meta={`${count} stack${count === 1 ? "" : "s"}`}
        right={
          <BulkWebhookButton
            collect={() => collectNodeWorkspaces(folder)}
            scopeLabel={`folder ${folder.name}`}
            onChanged={onChanged}
          />
        }
        className="border-t border-slate-100 dark:border-slate-800/50"
      />
      {open && (
        <FolderTreeBody
          node={folder}
          depth={depth + 1}
          latestByWs={latestByWs}
          onChanged={onChanged}
          expandSignal={expandSignal}
          awsAccounts={awsAccounts}
          azureSubscriptions={azureSubscriptions}
        />
      )}
    </div>
  );
}

// ─── Region group ────────────────────────────────────────────────────────────

function RegionGroup({
  region,
  workspaces,
  latestByWs,
  defaultOpen,
  onChanged,
  expandSignal,
  awsAccounts,
  azureSubscriptions,
}: {
  region: string;
  workspaces: Workspace[];
  latestByWs: Map<string, Run>;
  defaultOpen: boolean;
  onChanged: () => void;
  expandSignal: ExpandSignal;
  awsAccounts: AwsAccountLite[];
  azureSubscriptions: AzureSubscriptionLite[];
}) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => {
    if (expandSignal) setOpen(expandSignal.expand);
  }, [expandSignal?.version]);
  const tree = useMemo(() => buildFolderTree(workspaces), [workspaces]);
  return (
    <div>
      <TreeRow
        depth={1}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        icon={<FolderIcon open={open} />}
        label={<span className="font-mono">{region}</span>}
        meta={`${workspaces.length} stack${workspaces.length === 1 ? "" : "s"}`}
        right={
          <BulkWebhookButton
            collect={() => workspaces}
            scopeLabel={`region ${region}`}
            onChanged={onChanged}
          />
        }
        className="border-t border-slate-100 dark:border-slate-800/50"
      />
      {open && (
        <FolderTreeBody
          node={tree}
          depth={2}
          latestByWs={latestByWs}
          onChanged={onChanged}
          expandSignal={expandSignal}
          awsAccounts={awsAccounts}
          azureSubscriptions={azureSubscriptions}
        />
      )}
    </div>
  );
}

// ─── Cloud group card (shared by AWS account + Azure subscription) ─────────────

/**
 * Presentational top-level group card. Renders the icon + title + badge header
 * and the region subtree. Used for both AWS accounts (orange CloudIcon) and
 * Azure subscriptions (blue AzureIcon) so the two clouds share one layout and
 * the same Run/Destroy/webhook/expand affordances.
 */
function CloudGroupCard({
  icon,
  label,
  badge,
  scopeLabel,
  byRegion,
  latestByWs,
  defaultOpen,
  onChanged,
  expandSignal,
  awsAccounts,
  azureSubscriptions,
}: {
  icon: ReactNode;
  label: ReactNode;
  badge: ReactNode;
  scopeLabel: string;
  byRegion: Record<string, Workspace[]>;
  latestByWs: Map<string, Run>;
  defaultOpen: boolean;
  onChanged: () => void;
  expandSignal: ExpandSignal;
  awsAccounts: AwsAccountLite[];
  azureSubscriptions: AzureSubscriptionLite[];
}) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => {
    if (expandSignal) setOpen(expandSignal.expand);
  }, [expandSignal?.version]);
  const regionCount = Object.keys(byRegion).length;
  const total = Object.values(byRegion).reduce((s, w) => s + w.length, 0);
  return (
    <Card className="overflow-hidden">
      <TreeRow
        depth={0}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        icon={icon}
        label={<span className="font-semibold">{label}</span>}
        meta={
          <span>
            {regionCount} region{regionCount === 1 ? "" : "s"} · {total} stack
            {total === 1 ? "" : "s"}
          </span>
        }
        right={
          <>
            <BulkWebhookButton
              collect={() => Object.values(byRegion).flat()}
              scopeLabel={scopeLabel}
              onChanged={onChanged}
            />
            {badge}
          </>
        }
        className="bg-slate-50/80 dark:bg-slate-900/60"
      />
      {open && (
        <div>
          {Object.keys(byRegion)
            .sort()
            .map((region) => (
              <RegionGroup
                key={region}
                region={region}
                workspaces={byRegion[region]}
                latestByWs={latestByWs}
                defaultOpen={false}
                onChanged={onChanged}
                expandSignal={expandSignal}
                awsAccounts={awsAccounts}
                azureSubscriptions={azureSubscriptions}
              />
            ))}
        </div>
      )}
    </Card>
  );
}

// ─── Account group (AWS) ───────────────────────────────────────────────────────

export function AccountGroup({
  accountId,
  accountName,
  ...rest
}: {
  accountId: string;
  accountName?: string;
  byRegion: Record<string, Workspace[]>;
  latestByWs: Map<string, Run>;
  defaultOpen: boolean;
  onChanged: () => void;
  expandSignal: ExpandSignal;
  awsAccounts: AwsAccountLite[];
  azureSubscriptions: AzureSubscriptionLite[];
}) {
  return (
    <CloudGroupCard
      icon={<CloudIcon />}
      label={
        accountName ? (
          <>
            {accountName}{" "}
            <span className="ml-1 font-mono text-xs font-normal text-slate-500">{accountId}</span>
          </>
        ) : (
          <span className="font-mono">{accountId}</span>
        )
      }
      badge={
        !accountName ? (
          <Badge tone="warning">no AWS account</Badge>
        ) : (
          <Badge tone="success">configured</Badge>
        )
      }
      scopeLabel={accountName ? `${accountName} (${accountId})` : accountId}
      {...rest}
    />
  );
}

// ─── Subscription group (Azure) ────────────────────────────────────────────────

export function AzureSubscriptionGroup({
  sub,
  guid,
  ...rest
}: {
  // The registered subscription, when this group is linked/matched to one.
  sub?: AzureSubscriptionLite;
  // The subscription GUID parsed from the repo path when no registration
  // matches (workspaces synced but not yet linked / not registered).
  guid?: string;
  byRegion: Record<string, Workspace[]>;
  latestByWs: Map<string, Run>;
  defaultOpen: boolean;
  onChanged: () => void;
  expandSignal: ExpandSignal;
  awsAccounts: AwsAccountLite[];
  azureSubscriptions: AzureSubscriptionLite[];
}) {
  const subId = sub?.subscription_id ?? guid ?? "";
  return (
    <CloudGroupCard
      icon={<AzureIcon />}
      label={
        sub ? (
          <>
            {sub.name}{" "}
            <span className="ml-1 font-mono text-xs font-normal text-slate-500">{subId}</span>
          </>
        ) : (
          <span className="font-mono">subscription-{subId}</span>
        )
      }
      badge={
        sub ? (
          <Badge tone="success">configured</Badge>
        ) : (
          <Badge tone="warning">subscription not registered</Badge>
        )
      }
      scopeLabel={sub ? `${sub.name} (${subId})` : `subscription ${subId}`}
      {...rest}
    />
  );
}
