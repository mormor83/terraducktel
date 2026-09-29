// Which stacks are paired in an environment link — feeds the small "linked"
// glyph on Dashboard leaf rows. Dashboard fetches the index once and provides
// it; the default `{}` means every other tree consumer renders unchanged.

import { createContext, useContext } from "react";
import { Link } from "react-router-dom";

import type { StackIndex } from "../../api/envLinks";

export const StackIndexContext = createContext<StackIndex>({});

export function LinkedStackGlyph({ stackId }: { stackId: string }) {
  const index = useContext(StackIndexContext);
  const links = index[stackId];
  if (!links || links.length === 0) return null;
  const names = [...new Set(links.map((l) => l.link_name))].join(", ");
  return (
    <Link
      to={`/governance/environments/${links[0].link_id}`}
      title={`Linked in ${names}`}
      aria-label={`Linked in ${names}`}
      onClick={(e) => e.stopPropagation()}
      className="inline-flex h-5 w-5 items-center justify-center rounded text-brand-muted hover:text-[var(--td-green-ink)]"
    >
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <circle cx="18" cy="18" r="3" /><circle cx="6" cy="6" r="3" />
        <path d="M13 6h3a2 2 0 0 1 2 2v7" /><path d="M11 18H8a2 2 0 0 1-2-2V9" />
      </svg>
    </Link>
  );
}
