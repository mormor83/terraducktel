import { useEffect, useRef, useState } from "react";

import { useBusinessUnits, useBusinessUnitSelection } from "../hooks/useBusinessUnit";
import { useCurrentUser } from "../hooks/useAuth";
import { cx } from "./ui";

/**
 * Topbar Business Unit switcher.
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
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={loading || !!error}
        title="Business Unit scope"
        className={cx(
          "flex h-[34px] max-w-[220px] items-center gap-2 rounded-lg border border-brand-border bg-brand-surface px-3 text-[12.5px] text-brand-text",
          "transition-colors hover:border-brand-borderStrong hover:bg-brand-surface2",
          "disabled:opacity-60",
        )}
      >
        {/* textSoft, not muted: at 9.5px on the translucent topbar `muted` measured
            4.37:1 in dark — just under AA. */}
        <span className="shrink-0 font-mono text-[9.5px] tracking-[1.4px] text-brand-textSoft">BU</span>
        <span className="truncate font-medium">{currentLabel}</span>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="shrink-0 text-brand-muted" aria-hidden>
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && !loading && !error && (
        <div className="absolute right-0 top-full z-40 mt-2 w-64 overflow-hidden rounded-lg border border-brand-border bg-brand-surface shadow-[0_12px_32px_rgba(0,0,0,0.5)]">
          {user?.is_superadmin && (
            <button
              type="button"
              onClick={() => {
                setSelectedSlug("");
                setOpen(false);
              }}
              className={cx(
                "block w-full px-3 py-2 text-left text-sm text-brand-textSoft transition-colors hover:bg-brand-surface2 hover:text-brand-text",
                selectedSlug === "" && "bg-[rgba(182,255,75,0.1)] text-brand-text",
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
                  "block w-full px-3 py-2 text-left text-sm text-brand-textSoft transition-colors hover:bg-brand-surface2 hover:text-brand-text",
                  selectedSlug === b.slug && "bg-[rgba(182,255,75,0.1)] text-brand-text",
                )}
              >
                <div className="truncate font-medium">{b.name}</div>
                <div className="truncate font-mono text-[10.5px] text-brand-muted">{b.slug}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
