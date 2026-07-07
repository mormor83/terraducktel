import { api } from "./client";

export interface BusinessUnit {
  id: string;
  slug: string;
  name: string;
  created_at?: string;
}

export interface BusinessUnitCreate {
  slug: string;
  name: string;
}

// Fired whenever the list of BUs changes (create / rename / delete) so every
// open `useBusinessUnits` instance can refetch. The selection-change event
// lives in api/client.ts under "terraducktel:bu-changed".
export const BU_LIST_CHANGED_EVENT = "terraducktel:bu-list-changed";

function notifyBuListChanged() {
  window.dispatchEvent(new Event(BU_LIST_CHANGED_EVENT));
}

export async function listBusinessUnits(): Promise<BusinessUnit[]> {
  // Send the request without a stored BU header — the server filters by the
  // caller's identity (memberships for non-superadmin, everything for superadmin).
  const res = await api.get<BusinessUnit[]>("/v1/business-units", {
    headers: { "X-Business-Unit": "" },
  });
  return res.data;
}

export async function createBusinessUnit(body: BusinessUnitCreate): Promise<BusinessUnit> {
  const res = await api.post<BusinessUnit>("/v1/business-units", body);
  notifyBuListChanged();
  return res.data;
}

export async function updateBusinessUnit(
  id: string,
  body: { name: string },
): Promise<BusinessUnit> {
  // Slug is intentionally not editable — the backend ignores it on PUT.
  const res = await api.put<BusinessUnit>(`/v1/business-units/${id}`, body);
  notifyBuListChanged();
  return res.data;
}
