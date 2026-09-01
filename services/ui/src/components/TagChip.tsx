import { cx } from "./ui";

/**
 * A workspace tag rendered as `key=value`.
 *
 * Deliberately monochrome, unlike AccountTag: a workspace can carry a dozen
 * tags, and a dozen coloured chips on one row reads as noise and steals the
 * colour channel the account rail already owns. The key is dimmed and the value
 * is not, because in a list of `team=payments` / `team=ops` the value is the
 * part you scan for.
 *
 * Clicking filters, so it is a <button> — a chip that changes what you see and
 * isn't keyboard-reachable is a trap for anyone not using a mouse.
 */
export function TagChip({
  tagKey,
  value,
  onClick,
  active,
  className,
}: {
  tagKey: string;
  value: string;
  /** Omit to render a static chip (no hover, no focus ring, not tabbable). */
  onClick?: (tagKey: string, value: string) => void;
  active?: boolean;
  className?: string;
}) {
  const body = (
    <>
      <span className="text-slate-500 dark:text-slate-400">{tagKey}</span>
      {value !== "" && (
        <>
          <span className="text-slate-400 dark:text-slate-500">=</span>
          <span className="font-medium">{value}</span>
        </>
      )}
    </>
  );

  const base = cx(
    "inline-flex max-w-[16rem] items-center gap-0.5 truncate rounded px-1.5 py-0.5",
    "font-mono text-[10px] leading-4",
    active
      ? "bg-brand-500/15 text-brand-700 ring-1 ring-brand-500/40 dark:text-brand-200"
      : "bg-slate-500/10 text-slate-700 dark:text-slate-300",
    className,
  );

  if (!onClick) {
    return (
      <span className={base} title={`${tagKey}=${value}`}>
        {body}
      </span>
    );
  }
  return (
    <button
      type="button"
      className={cx(
        base,
        "transition hover:bg-slate-500/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500",
      )}
      title={
        active
          ? `Clear filter ${tagKey}=${value}`
          : `Filter by ${tagKey}=${value}`
      }
      aria-pressed={active}
      onClick={(e) => {
        // The row itself is clickable (expand/collapse); filtering shouldn't
        // also toggle the row open.
        e.stopPropagation();
        onClick(tagKey, value);
      }}
    >
      {body}
    </button>
  );
}

/**
 * A workspace's tags, sorted by key so the same workspace always reads the same
 * way, with an overflow marker rather than an unbounded wrap.
 */
export function TagList({
  tags,
  max = 3,
  onTagClick,
  activeTag,
  className,
}: {
  tags: Record<string, string> | null | undefined;
  /** Chips shown before collapsing into "+N". */
  max?: number;
  onTagClick?: (tagKey: string, value: string) => void;
  activeTag?: { key: string; value: string | null } | null;
  className?: string;
}) {
  const entries = Object.entries(tags ?? {}).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  if (entries.length === 0) return null;

  const shown = entries.slice(0, max);
  const hidden = entries.slice(max);

  return (
    <span className={cx("inline-flex flex-wrap items-center gap-1", className)}>
      {shown.map(([k, v]) => (
        <TagChip
          key={k}
          tagKey={k}
          value={v}
          onClick={onTagClick}
          active={
            !!activeTag &&
            activeTag.key === k &&
            (activeTag.value === null || activeTag.value === v)
          }
        />
      ))}
      {hidden.length > 0 && (
        <span
          className="rounded bg-slate-500/10 px-1.5 py-0.5 font-mono text-[10px] leading-4 text-slate-500 dark:text-slate-400"
          title={hidden.map(([k, v]) => `${k}=${v}`).join("\n")}
        >
          +{hidden.length}
        </span>
      )}
    </span>
  );
}
