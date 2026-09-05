import {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TableHTMLAttributes,
  TdHTMLAttributes,
  ThHTMLAttributes,
  useEffect,
  useRef,
} from "react";
import { Link } from "react-router-dom";

type ClassNames = (string | false | null | undefined)[];
export const cx = (...c: ClassNames): string => c.filter(Boolean).join(" ");

// ---------------------------------------------------------------------------
// Fantasy-tech theme. Token mapping (design var -> Tailwind utility):
//   panel   -> bg-brand-surface       line   -> border-brand-border
//   panel-2 -> bg-brand-surface2      line-2 -> border-brand-borderStrong
//   txt     -> text-brand-text        txt-2  -> text-brand-textSoft
//   txt-3   -> text-brand-muted       bg     -> brand-bg
// Every theme-dependent accent routes through a --td-* CSS var defined in
// src/index.css (light on :root, dark under [data-theme="dark"]/html.dark):
//   text/icon inks  -> var(--td-accent-ink|green-ink|run-ink|warn-ink|err-ink|info-ink)
//   washes/hairlines-> rgba(var(--td-edge-rgb|glow-rgb|ok-rgb|run-rgb|warn-rgb|err-rgb|info-rgb), α)
// The lime/green button FILLS (#b6ff4b / #7ed078 + dark ink) are deliberately
// literal — they are the brand punch and read correctly on both themes.
// ---------------------------------------------------------------------------

// -------------------------------------------------------------------------
// Card — `.card`: panel surface, hairline border, 14px radius. The optional
// `tick` prop renders the design's corner-bracket detail (9px L-shaped
// brackets, top-left + bottom-right). Off by default.
// -------------------------------------------------------------------------
const CARD_TICK =
  "before:pointer-events-none before:absolute before:-left-px before:-top-px before:h-[9px] before:w-[9px] before:rounded-tl-[14px] before:border-l before:border-t before:border-brand-borderStrong before:content-[''] " +
  "after:pointer-events-none after:absolute after:-bottom-px after:-right-px after:h-[9px] after:w-[9px] after:rounded-br-[14px] after:border-b after:border-r after:border-brand-borderStrong after:content-['']";

export function Card({
  tick = false,
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & {
  /** Renders the corner-tick ornament (top-left / bottom-right brackets). */
  tick?: boolean;
}) {
  return (
    <div
      className={cx(
        "relative rounded-[14px] border border-brand-border bg-brand-surface",
        tick && CARD_TICK,
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
        "flex items-center justify-between gap-3 border-b border-brand-border px-[18px] py-3.5",
        className,
      )}
      {...rest}
    />
  );
}

export function CardTitle({ className, ...rest }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cx("text-sm font-semibold text-brand-text", className)}
      {...rest}
    />
  );
}

export function CardBody({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx("p-[18px]", className)} {...rest} />;
}

// -------------------------------------------------------------------------
// Button — `.btn` family. 34px tall, 8px radius, 12.5px/600, 7px gap,
// 15px icons. `primary` is the lime call-to-action with a glow.
// -------------------------------------------------------------------------
type ButtonVariant = "primary" | "accent" | "secondary" | "ghost" | "danger" | "warning";
type ButtonSize = "sm" | "md";
const BTN_BASE =
  "inline-flex items-center justify-center gap-[7px] whitespace-nowrap rounded-md border font-semibold transition-all duration-150 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50 disabled:active:translate-y-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:ring-offset-brand-bg [&>svg]:h-[15px] [&>svg]:w-[15px] [&>svg]:shrink-0";
const BTN_VARIANT: Record<ButtonVariant, string> = {
  // .btn-p — lime primary with glow
  primary:
    "border-transparent bg-[#b6ff4b] text-[#0d1a05] shadow-[0_0_18px_-6px_rgba(182,255,75,.6)] hover:bg-[#c8ff6e] hover:shadow-[0_0_22px_-4px_rgba(182,255,75,.75)] active:bg-[#a8f03f] focus-visible:ring-[rgba(var(--td-glow-rgb),0.6)]",
  // green sibling of primary — distinct, calmer accent
  accent:
    "border-transparent bg-[#7ed078] text-[#0b1a0d] shadow-[0_0_18px_-8px_rgba(126,208,120,.55)] hover:bg-[#93dc8e] active:bg-[#6bc465] focus-visible:ring-[rgba(var(--td-edge-rgb),0.6)]",
  // .btn-s
  secondary:
    "border-brand-borderStrong bg-brand-surface2 text-brand-text hover:border-[rgba(var(--td-edge-rgb),0.35)] hover:bg-[var(--td-raise)] focus-visible:ring-[rgba(var(--td-edge-rgb),0.5)]",
  // .btn-g
  ghost:
    "border-transparent bg-transparent text-brand-textSoft hover:bg-[rgba(var(--td-edge-rgb),0.07)] hover:text-brand-text focus-visible:ring-[rgba(var(--td-edge-rgb),0.5)]",
  // .btn-d
  danger:
    "border-[rgba(var(--td-err-rgb),0.32)] bg-[rgba(var(--td-err-rgb),0.14)] text-[var(--td-err-ink)] hover:bg-[rgba(var(--td-err-rgb),0.22)] focus-visible:ring-[rgba(var(--td-err-rgb),0.6)]",
  // .btn-d shape on --warn
  warning:
    "border-[rgba(var(--td-warn-rgb),0.32)] bg-[rgba(var(--td-warn-rgb),0.14)] text-[var(--td-warn-ink)] hover:bg-[rgba(var(--td-warn-rgb),0.22)] focus-visible:ring-[rgba(var(--td-warn-rgb),0.6)]",
};
const BTN_SIZE: Record<ButtonSize, string> = {
  sm: "h-[27px] px-2.5 text-[11.5px]", // .btn-sm
  md: "h-[34px] px-3.5 text-[12.5px]",
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
// Input / Select / Label — match the design's `.search input` field.
// -------------------------------------------------------------------------
const FIELD_BASE =
  "block h-[34px] w-full rounded-md border border-brand-border bg-brand-surface px-3 text-[13px] text-brand-text transition-[border-color,box-shadow] duration-150 placeholder:text-brand-muted focus:border-brand-borderStrong focus:shadow-[0_0_0_3px_rgba(var(--td-glow-rgb),0.08)] focus:outline-none";

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
      className="mb-1.5 block font-mono text-[10px] font-medium uppercase tracking-[1.3px] text-brand-muted"
    >
      {children}
    </label>
  );
}

// -------------------------------------------------------------------------
// Badge — `.bdg`: 21px mono uppercase pills. Existing tone names are kept
// and mapped onto the design variants:
//   success -> b-ok · info -> b-run (lime) · warning + amber -> b-warn
//   danger -> b-err · neutral -> b-idle · violet -> the --info cyan
// `dot` renders the pulsing 5px dot (used by in-flight run statuses).
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
  neutral: "bg-[var(--td-idle-soft)] text-brand-muted",
  info: "bg-[rgba(var(--td-run-rgb),0.13)] text-[var(--td-run-ink)]",
  success: "bg-[rgba(var(--td-ok-rgb),0.12)] text-[var(--td-green-ink)]",
  warning: "bg-[rgba(var(--td-warn-rgb),0.13)] text-[var(--td-warn-ink)]",
  danger: "bg-[rgba(var(--td-err-rgb),0.13)] text-[var(--td-err-ink)]",
  violet: "bg-[rgba(var(--td-info-rgb),0.13)] text-[var(--td-info-ink)]",
  amber: "bg-[rgba(var(--td-warn-rgb),0.13)] text-[var(--td-warn-ink)]",
};

export function Badge({
  tone = "neutral",
  dot = false,
  children,
  className,
}: {
  tone?: BadgeTone;
  /** Pulsing 5px status dot (design's `.b-run .dot`). */
  dot?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex h-[21px] items-center gap-[5px] whitespace-nowrap rounded-full px-[9px] font-mono text-[10px] uppercase tracking-[.6px] [&>svg]:h-[11px] [&>svg]:w-[11px]",
        BADGE_TONE[tone],
        className,
      )}
    >
      {dot && (
        <span
          className="h-[5px] w-[5px] shrink-0 animate-[bp_1.2s_ease-in-out_infinite] rounded-full bg-current"
          aria-hidden
        />
      )}
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

/** Statuses that get the pulsing "in flight" dot. */
const RUN_STATUS_ACTIVE = new Set(["running", "planning", "applying"]);

export function RunStatusBadge({ status }: { status: string }) {
  const tone = RUN_STATUS_TONE[status] ?? "neutral";
  return (
    <Badge tone={tone} dot={RUN_STATUS_ACTIVE.has(status)}>
      {status.replace(/_/g, " ")}
    </Badge>
  );
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
// Table — `.tbl`: panel surface, hairline chrome, mono header row. Row
// hover + last-row border removal live on <Table> so <Td> stays simple.
// -------------------------------------------------------------------------
export function Table({ className, ...rest }: TableHTMLAttributes<HTMLTableElement>) {
  return (
    <table
      className={cx(
        "w-full border-collapse overflow-hidden rounded-[14px] border border-brand-border bg-brand-surface",
        "[&_tbody_tr]:transition-colors [&_tbody_tr:hover]:bg-[rgba(var(--td-edge-rgb),0.045)] [&_tbody_tr:last-child>td]:border-b-0",
        className,
      )}
      {...rest}
    />
  );
}

export function Th({ className, ...rest }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cx(
        "border-b border-brand-border bg-[var(--td-tint)] px-4 py-[11px] text-left font-mono text-[9.5px] font-medium uppercase tracking-[1.4px] text-brand-muted",
        className,
      )}
      {...rest}
    />
  );
}

export function Td({ className, ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      className={cx(
        "border-b border-[var(--td-row-border)] px-4 py-[13px] align-middle text-[13px]",
        className,
      )}
      {...rest}
    />
  );
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
    <div className="rounded-[14px] border border-dashed border-brand-borderStrong/70 bg-brand-surface/40 px-6 py-14 text-center">
      <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full border border-brand-border bg-[rgba(var(--td-ok-rgb),0.08)] text-[var(--td-green-ink)]">
        {icon ?? (
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M3 7l9-4 9 4-9 4-9-4Z" /><path d="M3 7v10l9 4 9-4V7" /><path d="m12 11 0 10" />
          </svg>
        )}
      </div>
      <h3 className="font-display text-base font-semibold text-brand-text">{title}</h3>
      {description && (
        <p className="mx-auto mt-1.5 max-w-sm text-[13px] text-brand-muted">{description}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

// -------------------------------------------------------------------------
// Skeleton
// -------------------------------------------------------------------------
export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded bg-[var(--td-skeleton)]", className)} />;
}

// -------------------------------------------------------------------------
// SectionHeader — `.phead`: Cinzel display title, mono green eyebrow.
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
    <div className="mb-[22px] flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && (
          <p className="mb-1 font-mono text-[10px] font-medium uppercase tracking-[1.6px] text-[var(--td-green-ink)]">
            {eyebrow}
          </p>
        )}
        <h1 className="font-display text-[29px] font-bold leading-[1.1] tracking-[.2px] text-brand-text">
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1.5 max-w-2xl text-[13px] text-brand-muted">{subtitle}</p>
        )}
      </div>
      {action}
    </div>
  );
}

// -------------------------------------------------------------------------
// Stat tiles — `.stat`: mono label, big Cinzel value, decorative offset
// circle (::after). `Stat` keeps its legacy API (label/value/hint/tone/to);
// `StatTile` is the design-faithful tile with an up/warn/flat delta line.
// -------------------------------------------------------------------------
const STAT_TILE =
  "relative overflow-hidden rounded-[14px] border border-brand-border bg-brand-surface px-[18px] py-4 " +
  "after:pointer-events-none after:absolute after:-right-6 after:-top-6 after:h-[90px] after:w-[90px] after:rounded-full after:border after:border-[rgba(var(--td-edge-rgb),0.09)] after:content-['']";

const STAT_VALUE_TONE: Record<BadgeTone, string> = {
  neutral: "text-brand-text",
  info: "text-[var(--td-run-ink)]",
  success: "text-[var(--td-green-ink)]",
  warning: "text-[var(--td-warn-ink)]",
  danger: "text-[var(--td-err-ink)]",
  violet: "text-[var(--td-info-ink)]",
  amber: "text-[var(--td-warn-ink)]",
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
  const interactive = to
    ? "transition-all hover:-translate-y-0.5 hover:border-brand-borderStrong"
    : "";
  const inner = (
    <div className={cx(STAT_TILE, "group", interactive)}>
      <div className="flex items-center gap-2">
        <p className="font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">{label}</p>
        {to && (
          <svg className="ml-auto shrink-0 text-brand-muted opacity-0 transition-opacity group-hover:opacity-100" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M7 17 17 7M9 7h8v8" />
          </svg>
        )}
      </div>
      <p className={cx("mt-2 font-display text-[31px] font-bold leading-[1.15] tabular-nums tracking-tight", STAT_VALUE_TONE[tone])}>
        {value}
      </p>
      {hint && <p className="mt-[5px] text-[11.5px] text-brand-muted">{hint}</p>}
    </div>
  );
  return to ? (
    <Link
      to={to}
      className="block rounded-[14px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(var(--td-glow-rgb),0.4)]"
    >
      {inner}
    </Link>
  ) : (
    inner
  );
}

type StatDeltaTone = "up" | "warn" | "flat";
const STAT_DELTA_TONE: Record<StatDeltaTone, string> = {
  up: "text-[var(--td-green-ink)]",
  warn: "text-[var(--td-warn-ink)]",
  flat: "text-brand-muted",
};

function DeltaIcon({ tone }: { tone: StatDeltaTone }) {
  const common = {
    width: 13,
    height: 13,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    className: "shrink-0",
  };
  if (tone === "up") {
    return (
      <svg {...common}>
        <path d="M3 17l6-6 4 4 8-8" /><path d="M14 7h7v7" />
      </svg>
    );
  }
  if (tone === "warn") {
    return (
      <svg {...common}>
        <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
        <path d="M12 9v4" /><path d="M12 17h.01" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M5 12h14" />
    </svg>
  );
}

export function StatTile({
  label,
  value,
  delta,
  deltaTone = "flat",
  className,
}: {
  label: string;
  value: ReactNode;
  /** Delta line under the value, e.g. "+3 this week". */
  delta?: ReactNode;
  deltaTone?: StatDeltaTone;
  className?: string;
}) {
  return (
    <div className={cx(STAT_TILE, className)}>
      <p className="font-mono text-[10px] uppercase tracking-[1.3px] text-brand-muted">{label}</p>
      <p className="mt-2 font-display text-[31px] font-bold leading-[1.15] tabular-nums text-brand-text">
        {value}
      </p>
      {delta && (
        <p className={cx("mt-[5px] flex items-center gap-[5px] text-[11.5px]", STAT_DELTA_TONE[deltaTone])}>
          <DeltaIcon tone={deltaTone} />
          {delta}
        </p>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------
// Ornament — `.orn`: the leaf divider (centered leaf glyph flanked by
// fading hairline rules). Purely decorative.
// -------------------------------------------------------------------------
export function Ornament({ className }: { className?: string }) {
  return (
    <div className={cx("mb-5 mt-1.5 flex items-center gap-3", className)} aria-hidden>
      <span className="h-px flex-1 bg-gradient-to-r from-transparent via-brand-borderStrong to-transparent" />
      <svg viewBox="0 0 40 18" className="h-3 w-[26px] shrink-0 text-[#1f6f6c] opacity-75" fill="currentColor">
        <path d="M20 1c-2.4 4-5.5 6.4-9 7.6 3.5 1.2 6.6 3.6 9 7.6 2.4-4 5.5-6.4 9-7.6-3.5-1.2-6.6-3.6-9-7.6z" />
        <path d="M8 8.6L0 8.6M40 8.6l-8 0" stroke="currentColor" strokeWidth="1" />
        <circle cx="10.5" cy="8.6" r="1.6" />
        <circle cx="29.5" cy="8.6" r="1.6" />
      </svg>
      <span className="h-px flex-1 bg-gradient-to-r from-transparent via-brand-borderStrong to-transparent" />
    </div>
  );
}

// -------------------------------------------------------------------------
// Spinner
// -------------------------------------------------------------------------
export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg
      className="animate-spin text-[var(--td-accent-ink)]"
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
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
    >
      <div
        ref={cardRef}
        className="w-full max-w-md rounded-[14px] border border-brand-borderStrong bg-brand-surface shadow-[0_18px_50px_rgba(0,0,0,.55)]"
      >
        <div className="border-b border-brand-border px-5 py-3">
          <h2 className="text-sm font-semibold text-brand-text">{title}</h2>
        </div>
        <div className="px-5 py-4 text-sm text-brand-textSoft">
          {message}
        </div>
        <div className="flex justify-end gap-2 border-t border-brand-border px-5 py-3">
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
