import { Suspense, lazy, useState } from "react";

import type { FileDiff } from "../../../api/envLinks";
import { Badge, Button, cx } from "../../ui";

const SideBySide = lazy(() => import("./SideBySide"));

const FILE_TONE: Record<FileDiff["status"], "success" | "danger" | "warning" | "neutral"> = {
  added: "success",
  removed: "danger",
  modified: "warning",
  unchanged: "neutral",
};

/** Unified diff with +/- line colouring. Diffs run target → source (+ = incoming). */
export function UnifiedDiff({ text, className }: { text: string; className?: string }) {
  if (!text.trim()) return <p className="text-[12px] italic text-brand-muted">No textual changes.</p>;
  return (
    <pre
      className={cx(
        "max-h-[460px] overflow-auto rounded-md border border-brand-border bg-brand-surface2 p-3 font-mono text-[11.5px] leading-[1.55]",
        className,
      )}
    >
      {text.split("\n").map((line, i) => (
        <div
          key={i}
          className={cx(
            line.startsWith("+") && !line.startsWith("+++") && "bg-[rgba(var(--td-ok-rgb),0.12)] text-[var(--td-green-ink)]",
            line.startsWith("-") && !line.startsWith("---") && "bg-[rgba(var(--td-err-rgb),0.1)] text-[var(--td-err-ink)]",
            line.startsWith("@@") && "text-[var(--td-info-ink)]",
            !/^[+\-@]/.test(line) && "text-brand-textSoft",
          )}
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}

function FileEntry({ f }: { f: FileDiff }) {
  const [open, setOpen] = useState(f.status !== "unchanged");
  const [side, setSide] = useState(false);
  return (
    <li className="rounded-md border border-brand-border">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="min-w-0 flex-1 truncate text-left font-mono text-[12px] text-brand-text"
        >
          {open ? "▾" : "▸"} {f.path}
        </button>
        {f.truncated && <span className="font-mono text-[10.5px] text-brand-muted">truncated</span>}
        <Badge tone={FILE_TONE[f.status]}>{f.status}</Badge>
        {open && f.status !== "unchanged" && (
          <Button variant="ghost" size="sm" onClick={() => setSide(!side)} aria-pressed={side}>
            {side ? "Unified" : "Side by side"}
          </Button>
        )}
      </div>
      {open && f.status !== "unchanged" && (
        <div className="border-t border-brand-border p-2">
          {side ? (
            f.source_text === null && f.target_text === null ? (
              <p className="text-[12px] italic text-brand-muted">Full text not kept for this file — use the unified view.</p>
            ) : (
              <Suspense fallback={<p className="text-[12px] italic text-brand-muted">Loading diff editor…</p>}>
                <p className="mb-1 font-mono text-[10.5px] text-brand-muted">left: target (current) · right: source (incoming)</p>
                <SideBySide original={f.target_text ?? ""} modified={f.source_text ?? ""} />
              </Suspense>
            )
          ) : (
            <UnifiedDiff text={f.unified} />
          )}
        </div>
      )}
    </li>
  );
}

export function RawDiff({ files }: { files: FileDiff[] }) {
  const changed = files.filter((f) => f.status !== "unchanged");
  const unchanged = files.length - changed.length;
  if (files.length === 0) return <p className="text-[13px] text-brand-muted">No files.</p>;
  return (
    <div>
      <ul className="space-y-2">
        {changed.map((f) => (
          <FileEntry key={f.path} f={f} />
        ))}
      </ul>
      {unchanged > 0 && (
        <p className="mt-2 font-mono text-[11px] text-brand-muted">
          {unchanged} unchanged file{unchanged === 1 ? "" : "s"}
        </p>
      )}
    </div>
  );
}
