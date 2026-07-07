import { useEffect, useRef, useState } from "react";

import { useBusinessUnits, useBusinessUnitSelection } from "../hooks/useBusinessUnit";
import { useCurrentUser } from "../hooks/useAuth";
import { cx } from "./ui";

/**
 * Sidebar Business Unit switcher.
 *
 * Renders nothing when only one BU is visible and the user is not a
 * superadmin — single-tenant deployments keep the existing chrome unchanged.
 *
 * Superadmins always see the switcher because they can choose "All BUs"
 * (no scope, system-wide view).
 */
export default function BusinessUnitSwitcher() {
  const user = useCurrentUser();
  const { bus, loading, error } = useBusinessUnits();
  const [selectedSlug, setSelectedSlug] = useBusinessUnitSelection();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Default the selection deterministically once BUs load.
  useEffect(() => {
    if (loading) return;
    if (bus.length === 0) return;
    if (selectedSlug !== null) return;
    if (user?.is_superadmin) {
      setSelectedSlug("");
    } else {
      setSelectedSlug(bus[0].slug);
    }
  }, [loading, bus, selectedSlug, user?.is_superadmin, setSelectedSlug]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const hideSwitcher =
    !loading && !error && bus.length <= 1 && !user?.is_superadmin;
  if (hideSwitcher) return null;

  const currentLabel = (() => {
    if (loading) return "Loading…";
    if (error) return "Unavailable";
    if (selectedSlug === "" || selectedSlug === null) {
      return user?.is_superadmin ? "All Business Units" : "(none)";
    }
    return bus.find((b) => b.slug === selectedSlug)?.name ?? selectedSlug;
  })();

  return (
    <div ref={ref} className="relative mb-4 px-3">
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-brand-muted">
        Business Unit
      </div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={loading || !!error}
        className={cx(
          "flex w-full items-center justify-between rounded-md border px-2.5 py-1.5 text-sm",
          "border-brand-border bg-brand-surface text-brand-text",
          "hover:bg-brand-surface2",
          "dark:border-brand-700 dark:bg-brand-800/40 dark:text-brand-100 dark:hover:bg-brand-800",
          "disabled:opacity-60",
        )}
      >
        <span className="truncate">{currentLabel}</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && !loading && !error && (
        <div className="absolute left-3 right-3 z-30 mt-1 overflow-hidden rounded-md border bg-brand-surface shadow-lg border-brand-border dark:border-brand-700 dark:bg-brand-900">
          {user?.is_superadmin && (
            <button
              type="button"
              onClick={() => {
                setSelectedSlug("");
                setOpen(false);
              }}
              className={cx(
                "block w-full px-3 py-2 text-left text-sm hover:bg-brand-surface2 dark:hover:bg-brand-800",
                selectedSlug === "" && "bg-brand-50 text-brand-700 dark:bg-brand-800/50 dark:text-brand-100",
              )}
            >
              All Business Units
            </button>
          )}
          <div className="max-h-64 overflow-y-auto">
            {bus.map((b) => (
              <button
                key={b.id}
                type="button"
                onClick={() => {
                  setSelectedSlug(b.slug);
                  setOpen(false);
                }}
                className={cx(
                  "block w-full px-3 py-2 text-left text-sm hover:bg-brand-surface2 dark:hover:bg-brand-800",
                  selectedSlug === b.slug && "bg-brand-50 text-brand-700 dark:bg-brand-800/50 dark:text-brand-100",
                )}
              >
                <div className="truncate font-medium">{b.name}</div>
                <div className="truncate text-[11px] text-brand-muted">{b.slug}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
