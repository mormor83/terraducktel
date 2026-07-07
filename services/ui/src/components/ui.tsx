import { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, useEffect, useRef } from "react";
import { Link } from "react-router-dom";

type ClassNames = (string | false | null | undefined)[];
export const cx = (...c: ClassNames): string => c.filter(Boolean).join(" ");

// -------------------------------------------------------------------------
// Card — frosted glass surface with subtle border + drop-shadow (dual-theme)
// -------------------------------------------------------------------------
export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cx(
        "rounded-xl border shadow-sm",
        // light
        "border-slate-200 bg-white",
        // dark
        "dark:border-slate-800/80 dark:bg-slate-900/60 dark:backdrop-blur-sm dark:shadow-lg dark:shadow-black/20",
        className,
      )}
      {...rest}
    />
  );
}

export function CardHeader({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cx(
        "flex items-center justify-between gap-3 border-b px-5 py-4",
        "border-slate-200 dark:border-slate-800/80",
        className,
      )}
      {...rest}
    />
  );
}

export function CardTitle({ className, ...rest }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cx(
        "text-base font-semibold",
        "text-slate-900 dark:text-slate-100",
        className,
      )}
      {...rest}
    />
  );
}

export function CardBody({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx("p-5", className)} {...rest} />;
}

// -------------------------------------------------------------------------
// Button
// -------------------------------------------------------------------------
type ButtonVariant = "primary" | "accent" | "secondary" | "ghost" | "danger" | "warning";
type ButtonSize = "sm" | "md";
const BTN_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-all duration-150 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50 disabled:active:translate-y-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:ring-offset-brand-surface dark:focus-visible:ring-offset-brand-ink";
const BTN_VARIANT: Record<ButtonVariant, string> = {
  primary:
    "bg-brand-500 text-white hover:bg-brand-600 active:bg-brand-700 focus-visible:ring-brand-400 shadow-sm dark:bg-brand-500 dark:hover:bg-brand-400 dark:active:bg-brand-600",
  accent:
    "bg-accent-400 text-brand-900 hover:bg-accent-300 active:bg-accent-500 focus-visible:ring-accent-400 shadow-sm dark:bg-accent-400 dark:hover:bg-accent-300 dark:active:bg-accent-500",
  secondary:
    "bg-brand-surface text-brand-text border border-brand-borderStrong hover:bg-brand-surface2 hover:border-brand-muted focus-visible:ring-brand-400 dark:bg-brand-800/40 dark:text-brand-100 dark:border-brand-700 dark:hover:bg-brand-800 dark:hover:border-brand-600",
  ghost:
    "bg-transparent text-brand-textSoft hover:bg-brand-surface2 hover:text-brand-text focus-visible:ring-brand-400 dark:text-brand-100/80 dark:hover:bg-brand-800/40 dark:hover:text-brand-100",
  danger:
    "bg-[#c4452f] text-white hover:bg-[#a63a27] active:bg-[#8a2f1f] focus-visible:ring-[#c4452f]/60",
  warning:
    "bg-[#c98a14] text-white hover:bg-[#a87311] active:bg-[#8a5e0e] focus-visible:ring-[#c98a14]/60",
};
const BTN_SIZE: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-xs",
  md: "h-9 px-3.5 text-sm",
};

export function Button({
  variant = "primary",
  size = "md",
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
}) {
  return (
    <button className={cx(BTN_BASE, BTN_VARIANT[variant], BTN_SIZE[size], className)} {...rest} />
  );
}

// -------------------------------------------------------------------------
// Input / Select / Label
// -------------------------------------------------------------------------
const FIELD_BASE =
  "block w-full rounded-md px-3 py-2 text-sm transition-colors focus:outline-none focus:ring-2 " +
  "border border-brand-border bg-white text-brand-text placeholder-brand-muted focus:border-brand-400 focus:ring-brand-400/30 " +
  "dark:border-slate-700/70 dark:bg-slate-950/60 dark:text-slate-100 dark:placeholder-slate-500 dark:focus:border-brand-400 dark:focus:ring-brand-400/30";

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(FIELD_BASE, className)} {...rest} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(FIELD_BASE, "appearance-none pr-8", className)} {...rest}>
      {children}
    </select>
  );
}

export function Label({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  return (
    <label
      htmlFor={htmlFor}
      className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-slate-500 dark:text-slate-400"
    >
      {children}
    </label>
  );
}

// -------------------------------------------------------------------------
// Badge — colored pills for statuses
// -------------------------------------------------------------------------
type BadgeTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "violet"
  | "amber";

const BADGE_TONE: Record<BadgeTone, string> = {
  neutral:
    "bg-slate-100 text-slate-700 ring-slate-300/80 dark:bg-slate-800/80 dark:text-slate-300 dark:ring-slate-700/50",
  info:
    "bg-sky-50 text-sky-700 ring-sky-300/60 dark:bg-sky-900/40 dark:text-sky-300 dark:ring-sky-700/40",
  success:
    "bg-emerald-50 text-emerald-700 ring-emerald-300/60 dark:bg-emerald-900/40 dark:text-emerald-300 dark:ring-emerald-700/40",
  warning:
    "bg-amber-50 text-amber-700 ring-amber-300/60 dark:bg-amber-900/40 dark:text-amber-300 dark:ring-amber-700/40",
  danger:
    "bg-red-50 text-red-700 ring-red-300/60 dark:bg-red-900/40 dark:text-red-300 dark:ring-red-700/40",
  violet:
    "bg-violet-50 text-violet-700 ring-violet-300/60 dark:bg-violet-900/40 dark:text-violet-300 dark:ring-violet-700/40",
  amber:
    "bg-amber-100 text-amber-800 ring-amber-400/60 dark:bg-amber-500/20 dark:text-amber-300 dark:ring-amber-600/40",
};

export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        BADGE_TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const RUN_STATUS_TONE: Record<string, BadgeTone> = {
  pending: "neutral",
  running: "info",
  planning: "info",
  planned: "violet",
  awaiting_approval: "amber",
  applying: "warning",
  applied: "success",
  failed: "danger",
  cancelled: "neutral",
};

export function RunStatusBadge({ status }: { status: string }) {
  const tone = RUN_STATUS_TONE[status] ?? "neutral";
  return <Badge tone={tone}>{status.replace(/_/g, " ")}</Badge>;
}

const DRIFT_TONE: Record<string, BadgeTone> = {
  clean: "success",
  drifted: "danger",
  unknown: "neutral",
};

export function DriftBadge({ status }: { status: string }) {
  const tone = DRIFT_TONE[status] ?? "neutral";
  return <Badge tone={tone}>{status}</Badge>;
}

// -------------------------------------------------------------------------
// EmptyState
// -------------------------------------------------------------------------
export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-dashed border-brand-borderStrong/70 bg-gradient-to-b from-brand-surface2/50 to-transparent px-6 py-14 text-center dark:border-slate-700/70 dark:from-slate-800/30">
      <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full bg-brand-100 text-brand-500 dark:bg-brand-500/15 dark:text-brand-300">
        {icon ?? (
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M3 7l9-4 9 4-9 4-9-4Z" /><path d="M3 7v10l9 4 9-4V7" /><path d="m12 11 0 10" />
          </svg>
        )}
      </div>
      <h3 className="font-display text-base font-semibold text-brand-text dark:text-slate-100">{title}</h3>
      {description && (
        <p className="mx-auto mt-1.5 max-w-sm text-sm text-brand-muted dark:text-slate-400">{description}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

// -------------------------------------------------------------------------
// Skeleton
// -------------------------------------------------------------------------
export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={cx("animate-pulse rounded bg-slate-200 dark:bg-slate-800/60", className)} />
  );
}

// -------------------------------------------------------------------------
// SectionHeader
// -------------------------------------------------------------------------
export function SectionHeader({
  title,
  subtitle,
  eyebrow,
  action,
}: {
  title: string;
  subtitle?: ReactNode;
  eyebrow?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && (
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-500 dark:text-brand-300">
            {eyebrow}
          </p>
        )}
        <h1 className="font-display text-3xl font-semibold tracking-tight text-brand-text dark:text-slate-100">{title}</h1>
        {subtitle && (
          <p className="mt-1.5 max-w-2xl text-sm text-brand-muted dark:text-slate-400">{subtitle}</p>
        )}
      </div>
      {action}
    </div>
  );
}

// -------------------------------------------------------------------------
// Stat card
// -------------------------------------------------------------------------
const STAT_TONE: Record<BadgeTone, { value: string; rail: string; tint: string; dot: string }> = {
  neutral: { value: "text-brand-text dark:text-slate-100", rail: "bg-brand-300 dark:bg-slate-600", tint: "from-brand-surface2/60 dark:from-slate-800/30", dot: "bg-brand-400" },
  info: { value: "text-brand-700 dark:text-brand-200", rail: "bg-brand-400", tint: "from-brand-50 dark:from-brand-500/10", dot: "bg-brand-400" },
  success: { value: "text-accent-700 dark:text-accent-300", rail: "bg-accent-400", tint: "from-accent-50 dark:from-accent-500/10", dot: "bg-accent-500" },
  warning: { value: "text-amber-700 dark:text-amber-300", rail: "bg-amber-400", tint: "from-amber-50 dark:from-amber-500/10", dot: "bg-amber-500" },
  danger: { value: "text-red-700 dark:text-red-300", rail: "bg-red-400", tint: "from-red-50 dark:from-red-500/10", dot: "bg-red-500" },
  violet: { value: "text-violet-700 dark:text-violet-300", rail: "bg-violet-400", tint: "from-violet-50 dark:from-violet-500/10", dot: "bg-violet-500" },
  amber: { value: "text-amber-700 dark:text-amber-300", rail: "bg-amber-400", tint: "from-amber-50 dark:from-amber-500/10", dot: "bg-amber-500" },
};

export function Stat({
  label,
  value,
  hint,
  tone = "neutral",
  to,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: BadgeTone;
  /** When set, the whole tile becomes a router link to this path. */
  to?: string;
}) {
  const t = STAT_TONE[tone];
  const interactive = to
    ? "cursor-pointer hover:-translate-y-0.5 hover:shadow-td-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/50"
    : "";
  const inner = (
    <Card className={cx("group relative block overflow-hidden bg-gradient-to-br to-transparent p-5 shadow-td-sm transition-all", t.tint, interactive)}>
      <span className={cx("absolute inset-x-0 top-0 h-[3px]", t.rail)} aria-hidden />
      <div className="flex items-center gap-2">
        <span className={cx("h-1.5 w-1.5 rounded-full", t.dot)} aria-hidden />
        <p className="text-[11px] font-medium uppercase tracking-wider text-brand-muted dark:text-slate-500">
          {label}
        </p>
        {to && (
          <svg className="ml-auto text-brand-muted opacity-0 transition-opacity group-hover:opacity-100" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M7 17 17 7M9 7h8v8" />
          </svg>
        )}
      </div>
      <p className={cx("mt-2 font-display text-3xl font-semibold tabular-nums tracking-tight", t.value)}>{value}</p>
      {hint && <p className="mt-1 text-xs text-brand-muted dark:text-slate-500">{hint}</p>}
    </Card>
  );
  return to ? <Link to={to} className="block rounded-xl">{inner}</Link> : inner;
}

// -------------------------------------------------------------------------
// Spinner
// -------------------------------------------------------------------------
export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg
      className="animate-spin text-sky-500 dark:text-sky-400"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle className="opacity-30" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

// -------------------------------------------------------------------------
// RunningDuck — the animated brand mark (wings flapping) used as a lively
// "in progress, not stuck" indicator. Reuses the single-source SVG at
// /td/brand/terraducktel-mark.svg, whose SMIL animation runs inside <img>.
// The mark's viewBox is ~1.8:1, so we size by height and let width follow.
// -------------------------------------------------------------------------
export function RunningDuck({ size = 16, className, title = "Running" }: { size?: number; className?: string; title?: string }) {
  return (
    <img
      src="/td/brand/terraducktel-mark.svg"
      alt=""
      aria-hidden
      title={title}
      height={size}
      style={{ height: size, width: "auto" }}
      className={cx("inline-block shrink-0 select-none", className)}
    />
  );
}

// -------------------------------------------------------------------------
// ConfirmDialog — in-app confirmation modal (replaces window.confirm)
// -------------------------------------------------------------------------
type ConfirmTone = "primary" | "danger" | "warning" | "accent";

const CONFIRM_VARIANT: Record<ConfirmTone, "primary" | "danger" | "warning" | "accent"> = {
  primary: "primary",
  danger: "danger",
  warning: "warning",
  accent: "accent",
};

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "primary",
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const cardRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onCancel();
      if (e.key === "Enter" && !busy) onConfirm();
    }
    function onClick(e: MouseEvent) {
      if (busy) return;
      if (cardRef.current && !cardRef.current.contains(e.target as Node)) onCancel();
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [open, busy, onCancel, onConfirm]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4 backdrop-blur-sm"
    >
      <div
        ref={cardRef}
        className="w-full max-w-md rounded-lg border border-brand-borderStrong bg-brand-surface shadow-xl dark:border-brand-700 dark:bg-brand-900"
      >
        <div className="border-b border-brand-borderStrong px-5 py-3 dark:border-brand-700">
          <h2 className="text-sm font-semibold text-brand-text dark:text-brand-100">{title}</h2>
        </div>
        <div className="px-5 py-4 text-sm text-brand-textSoft dark:text-brand-100/80">
          {message}
        </div>
        <div className="flex justify-end gap-2 border-t border-brand-borderStrong px-5 py-3 dark:border-brand-700">
          <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant={CONFIRM_VARIANT[tone]}
            size="sm"
            onClick={onConfirm}
            disabled={busy}
            autoFocus
          >
            {busy ? <Spinner size={14} /> : null}
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
