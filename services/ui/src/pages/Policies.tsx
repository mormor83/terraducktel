import { Suspense, lazy } from "react";

import { EmptyState, SectionHeader } from "../components/ui";
import { useBusinessUnitSelection } from "../hooks/useBusinessUnit";

// Same lazy pattern as Settings' `policies` tab: the editor (Rego editing,
// dry-run, history diffing) is heavy, so it only loads when this page mounts.
// Both entry points render the same self-contained component — this page is a
// second door to one room, not duplicated state.
const PoliciesSection = lazy(() => import("../components/settings/PoliciesSection"));

export default function Policies() {
  // Policies are BU-scoped and the backend 400s on the cross-BU view
  // ("all" / no selection — the superadmin default), so gate the editor on a
  // specific BU instead of letting those 400s surface as error banners.
  // `""` is the legal "all BUs" value and `null` is "not yet chosen" — both
  // are falsy, and neither is a specific BU.
  const [buSlug] = useBusinessUnitSelection();

  return (
    <div>
      <SectionHeader
        eyebrow="GOVERNANCE"
        title="Policies"
        subtitle="OPA/conftest rules evaluated against every plan — enforce, warn, or disable per Business Unit."
      />
      {buSlug ? (
        <Suspense fallback={<p className="text-sm italic text-slate-500">Loading editor…</p>}>
          {/* Key on the BU so a switch re-mounts the editor with fresh data. */}
          <PoliciesSection key={buSlug} />
        </Suspense>
      ) : (
        <EmptyState
          title="Select a Business Unit"
          description="Policies are scoped per Business Unit. Pick a specific BU in the switcher in the top bar to view and manage its policy rules."
          icon={
            <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
              <path d="M12 1 3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4Zm0 5a2.5 2.5 0 0 1 2.5 2.5c0 1-.6 1.7-1.2 2.2-.5.4-.8.7-.8 1.3h-1c0-1 .5-1.6 1.1-2.1.5-.4.9-.7.9-1.4A1.5 1.5 0 0 0 12 7a1.5 1.5 0 0 0-1.5 1.5h-1A2.5 2.5 0 0 1 12 6Zm-.5 8h1v1h-1v-1Z" />
            </svg>
          }
        />
      )}
    </div>
  );
}
