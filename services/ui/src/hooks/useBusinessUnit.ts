import { useEffect, useState } from "react";

import {
  BU_LIST_CHANGED_EVENT,
  listBusinessUnits,
  type BusinessUnit,
} from "../api/businessUnits";
import { getCurrentBusinessUnit, setCurrentBusinessUnit } from "../api/client";

const BU_CHANGED_EVENT = "terraducktel:bu-changed";

/**
 * Current Business Unit selection — backed by localStorage.
 *
 * Returns the slug string. `""` is the legal "all BUs" value (only meaningful
 * for superadmin users); `null` means "not yet chosen". The setter persists
 * to storage and fires a window event so other components re-render.
 */
export function useBusinessUnitSelection(): [string | null, (slug: string | null) => void] {
  const [bu, setBu] = useState<string | null>(getCurrentBusinessUnit());

  useEffect(() => {
    const handler = () => setBu(getCurrentBusinessUnit());
    window.addEventListener(BU_CHANGED_EVENT, handler);
    window.addEventListener("storage", handler);
    return () => {
      window.removeEventListener(BU_CHANGED_EVENT, handler);
      window.removeEventListener("storage", handler);
    };
  }, []);

  return [bu, setCurrentBusinessUnit];
}

/** Fetches the list of BUs visible to the current user. */
export function useBusinessUnits(): {
  bus: BusinessUnit[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
} {
  const [bus, setBus] = useState<BusinessUnit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listBusinessUnits()
      .then((rows) => {
        if (!cancelled) {
          setBus(rows);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load business units");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  // Re-fetch when *any* code path mutates the BU list (create, rename, …).
  // Multiple components hold their own copy of this hook — the sidebar
  // switcher and the BusinessUnits page each call useBusinessUnits — so we
  // can't rely on whoever performed the mutation to refresh everyone else.
  useEffect(() => {
    const onListChanged = () => setTick((n) => n + 1);
    window.addEventListener(BU_LIST_CHANGED_EVENT, onListChanged);
    return () => window.removeEventListener(BU_LIST_CHANGED_EVENT, onListChanged);
  }, []);

  return { bus, loading, error, refresh: () => setTick((n) => n + 1) };
}
