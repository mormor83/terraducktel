import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { AccountColor } from "../components/accountColors";
import { asAccountColor } from "../components/accountColors";
import { BU_CHANGED_EVENT } from "./useBusinessUnit";

/**
 * Resolves the cloud account behind a workspace so run rows can be colour-coded
 * and labelled by account instead of by bare 12-digit id.
 *
 * All four provider lists are readable by `viewer` and return no secrets, so
 * this works for every role. They're fetched once (not on the Runs page's 8s
 * poll) — accounts change on the order of months, runs on the order of seconds.
 */

/** What a run row needs to render an account: a name, an id, and a colour. */
export type AccountBadge = {
  color: AccountColor;
  /** Display name, e.g. "Prod-Account". */
  name: string;
  /** Natural id — the 12-digit AWS account, subscription/project id, or cluster PK. */
  id: string;
};

/** The workspace fields this resolver keys off; a subset of WorkspaceResponse. */
export type WorkspaceLike = {
  kind?: string;
  aws_account_id?: string | null;
  azure_subscription_id?: string | null;
  gcp_project_id?: string | null;
  cluster_id?: string | null;
};

type Maps = {
  aws: Record<string, AccountBadge>;
  azure: Record<string, AccountBadge>;
  gcp: Record<string, AccountBadge>;
  k8s: Record<string, AccountBadge>;
};

const EMPTY: Maps = { aws: {}, azure: {}, gcp: {}, k8s: {} };

export function useAccountColors(): {
  /** null when the workspace has no attributable account (or it was deleted). */
  badgeFor: (ws: WorkspaceLike | undefined) => AccountBadge | null;
  loading: boolean;
} {
  const [maps, setMaps] = useState<Maps>(EMPTY);
  const [loading, setLoading] = useState(true);
  // Bumped on a BU switch to force a refetch. The switcher doesn't reload the
  // page, so without this the maps would keep the previous BU's accounts and
  // every row in the new BU would fall back to a bare account id.
  const [epoch, setEpoch] = useState(0);

  useEffect(() => {
    const bump = () => setEpoch((n) => n + 1);
    window.addEventListener(BU_CHANGED_EVENT, bump);
    // "storage" covers a BU switched in another tab.
    window.addEventListener("storage", bump);
    return () => {
      window.removeEventListener(BU_CHANGED_EVENT, bump);
      window.removeEventListener("storage", bump);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      // A provider the deployment doesn't use 404s/403s or returns [] — each
      // call degrades to an empty map independently so one missing provider
      // never costs the others their colours.
      const empty = { data: [] as any[] };
      const [aws, azure, gcp, k8s] = await Promise.all([
        api.get("/v1/aws-accounts").catch(() => empty),
        api.get("/v1/azure-subscriptions").catch(() => empty),
        api.get("/v1/gcp-projects").catch(() => empty),
        api.get("/v1/clusters").catch(() => empty),
      ]);
      if (!alive) return;
      const build = (rows: any[], key: (r: any) => string): Record<string, AccountBadge> => {
        const out: Record<string, AccountBadge> = {};
        for (const r of rows ?? []) {
          out[key(r)] = {
            color: asAccountColor(r.color_effective ?? r.color),
            name: r.name ?? "",
            id: key(r),
          };
        }
        return out;
      };
      setMaps({
        // AWS is keyed by the 12-digit account id because that's what
        // `workspace.aws_account_id` stores — not the row PK, unlike the others.
        aws: build(aws.data, (r) => r.account_id),
        azure: build(azure.data, (r) => r.id),
        gcp: build(gcp.data, (r) => r.id),
        k8s: build(k8s.data, (r) => r.id),
      });
      setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, [epoch]);

  const badgeFor = useCallback(
    (ws: WorkspaceLike | undefined): AccountBadge | null => {
      if (!ws) return null;
      // Order mirrors notification_service._account_badge so a run's colour is
      // the same in the UI and in Slack.
      if (ws.kind === "helm" && ws.cluster_id) return maps.k8s[ws.cluster_id] ?? null;
      if (ws.azure_subscription_id) return maps.azure[ws.azure_subscription_id] ?? null;
      if (ws.gcp_project_id) return maps.gcp[ws.gcp_project_id] ?? null;
      // "global" is the sentinel for provider-less workspaces — not an account.
      if (ws.aws_account_id && ws.aws_account_id !== "global") {
        return maps.aws[ws.aws_account_id] ?? null;
      }
      return null;
    },
    [maps],
  );

  return { badgeFor, loading };
}
