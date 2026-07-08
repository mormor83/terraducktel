// Inline SVG icons for the workspace tree rows. Kept together so the tree's
// visual vocabulary (chevron / folder / file / cloud) lives in one place.

import { cx } from "../ui";

export function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="currentColor"
      className={cx("transition-transform", open ? "rotate-90" : "rotate-0", "text-slate-500")}
      aria-hidden
    >
      <path d="M9 6l6 6-6 6V6Z" />
    </svg>
  );
}

export function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden className={open ? "text-sky-500" : "text-amber-500/80 dark:text-amber-400/80"}>
      {open ? (
        <path d="M2 7a2 2 0 0 1 2-2h5l2 2h9a2 2 0 0 1 2 2v1H4l-2 9V7Zm0 11 2-9h20l-2 9H2Z" />
      ) : (
        <path d="M2 6a2 2 0 0 1 2-2h5l2 2h9a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6Z" />
      )}
    </svg>
  );
}

export function FileIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden className="text-slate-400 dark:text-slate-500">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm0 7V3.5L19.5 9H14Z" />
    </svg>
  );
}

// Small chip rendered on a helm-kind workspace leaf, next to the branch chip.
// Terraform leaves render nothing extra (kind defaults to terraform).
export function HelmChip() {
  return (
    <span
      title="Helm workspace — plan/apply/destroy map to helm diff/upgrade/uninstall against its cluster (no S3 state backend)."
      className="inline-flex shrink-0 items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-[11px] font-medium text-blue-800 ring-1 ring-inset ring-blue-300/70 dark:bg-blue-900/30 dark:text-blue-200 dark:ring-blue-700/50"
    >
      <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden className="shrink-0">
        <path d="M12 2 3 6.5v11L12 22l9-4.5v-11L12 2Zm0 2.3 6.5 3.25L12 10.8 5.5 7.55 12 4.3ZM5 9.2l6 3v6.6l-6-3V9.2Zm14 0v6.6l-6 3v-6.6l6-3Z" />
      </svg>
      helm
    </span>
  );
}

export function CloudIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden className="text-orange-500 dark:text-orange-400">
      <path d="M19.35 10.04A7.49 7.49 0 0 0 12 4a7.5 7.5 0 0 0-6.94 4.66A6 6 0 0 0 6 20h13a5 5 0 0 0 .35-9.96Z" />
    </svg>
  );
}

// Azure subscription group icon — the canonical Azure "A" mark in Azure blue,
// to read as visually distinct from the orange AWS CloudIcon at a glance.
export function AzureIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden className="text-blue-500 dark:text-blue-400">
      <path d="M5.483 21.3H24L14.025 4.013l-3.038 8.347 5.836 6.938L5.483 21.3zM13.23 2.7 6.105 8.677 0 19.253h5.505v.014L13.23 2.7z" />
    </svg>
  );
}

// GCP project group icon — a cloud+check in emerald, distinct from the orange
// AWS CloudIcon and blue AzureIcon at a glance. Stroke-based (matches the
// `td-i-gcp` sprite geometry) rather than filled.
export function GcpIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className="text-emerald-500 dark:text-emerald-400"
    >
      <path d="M7 17a4 4 0 0 1 1-7.9 6 6 0 0 1 11 2A3.5 3.5 0 0 1 18 18z" />
      <path d="M10 13l2 2 3-4" />
    </svg>
  );
}
