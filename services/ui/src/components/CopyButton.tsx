import { useState } from "react";

type Props = {
  getText: () => string;
  label?: string;
  title?: string;
  className?: string;
};

/**
 * Small "copy to clipboard" button used next to expandable output panels
 * (Plan Summary, step output viewers). Provides a 1.5s "Copied" affordance
 * and falls back gracefully when navigator.clipboard is unavailable.
 */
export default function CopyButton({ getText, label = "Copy", title, className }: Props) {
  const [copied, setCopied] = useState(false);

  async function onClick() {
    const text = getText();
    if (!text) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Best-effort — surface nothing on failure; clipboard perms are env-specific.
    }
  }

  return (
    <button
      type="button"
      onClick={onClick}
      title={title ?? "Copy all content to clipboard"}
      className={
        className ??
        "inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
      }
    >
      {copied ? (
        <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2Z" />
        </svg>
      ) : (
        <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12V1Zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2Zm0 16H8V7h11v14Z" />
        </svg>
      )}
      {copied ? "Copied" : label}
    </button>
  );
}
