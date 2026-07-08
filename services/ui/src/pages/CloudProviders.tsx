import { useState } from "react";
import AwsAccounts from "./AwsAccounts";
import AzureSubscriptions from "./AzureSubscriptions";
import GcpProjects from "./GcpProjects";
import Clusters from "./Clusters";

/**
 * Cloud resources settings tab.
 *
 * Hosts a sub-tab per resource type: AWS accounts, Azure subscriptions, GCP
 * projects, and Kubernetes clusters (for Helm workspaces). One place for all
 * infra creds — future providers (Cloudflare, …) drop in here without
 * re-shaping Settings.
 */
const PROVIDERS = [
  { id: "aws", label: "AWS", render: () => <AwsAccounts /> },
  { id: "azure", label: "Azure", render: () => <AzureSubscriptions /> },
  { id: "gcp", label: "GCP", render: () => <GcpProjects /> },
  { id: "clusters", label: "Kubernetes", render: () => <Clusters /> },
] as const;

type ProviderId = (typeof PROVIDERS)[number]["id"];

export default function CloudProviders() {
  const [active, setActive] = useState<ProviderId>("aws");
  const current = PROVIDERS.find((p) => p.id === active) ?? PROVIDERS[0];
  return (
    <div>
      <div className="mb-4 inline-flex rounded-md border border-slate-200 bg-white p-0.5 dark:border-slate-700 dark:bg-slate-900">
        {PROVIDERS.map((p) => {
          const isActive = p.id === active;
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setActive(p.id)}
              className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
                isActive
                  ? "bg-sky-500 text-white"
                  : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              }`}
            >
              {p.label}
            </button>
          );
        })}
      </div>
      {current.render()}
    </div>
  );
}
