/**
 * Monaco-based rego editor + diff view, bundled locally (no CDN) so TDT keeps
 * its self-hosted / no-SaaS guarantee. We register a minimal `rego` language
 * (keywords, strings, comments, numbers) — enough for authoring OPA policies.
 *
 * The editor worker is imported via Vite's `?worker` so it ships in our own
 * bundle; we don't need the TS/JSON language workers for a custom language.
 */
import { useEffect, useState } from "react";
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import { Editor, DiffEditor, loader } from "@monaco-editor/react";

// Wire Monaco to use our bundled instance + worker instead of fetching from a CDN.
let _configured = false;
function configureMonacoOnce() {
  if (_configured) return;
  _configured = true;
  // MonacoEnvironment is a global Monaco reads at startup to locate its worker.
  (self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
    getWorker: () => new editorWorker(),
  };
  loader.config({ monaco });

  if (!monaco.languages.getLanguages().some((l) => l.id === "rego")) {
    monaco.languages.register({ id: "rego" });
    monaco.languages.setMonarchTokensProvider("rego", {
      keywords: [
        "package", "import", "as", "default", "else", "false", "true", "null",
        "not", "with", "some", "in", "every", "if", "contains",
      ],
      builtins: ["deny", "warn", "violation", "allow", "input", "data", "sprintf", "count"],
      tokenizer: {
        root: [
          [/#.*$/, "comment"],
          [/"(?:[^"\\]|\\.)*"/, "string"],
          [/`[^`]*`/, "string"],
          [/\b\d+(\.\d+)?\b/, "number"],
          [
            /[a-zA-Z_]\w*/,
            {
              cases: {
                "@keywords": "keyword",
                "@builtins": "type",
                "@default": "identifier",
              },
            },
          ],
          [/[{}()\[\]]/, "@brackets"],
          [/[:=!<>|&+\-*/]+/, "operator"],
        ],
      },
    });
  }
}

const COMMON = {
  minimap: { enabled: false },
  fontSize: 13,
  lineNumbers: "on" as const,
  scrollBeyondLastLine: false,
  automaticLayout: true,
  tabSize: 2,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
};

function useDarkTheme(): "vs-dark" | "light" {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const obs = new MutationObserver(() =>
      setDark(document.documentElement.classList.contains("dark")),
    );
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark ? "vs-dark" : "light";
}

export function RegoEditor({
  value,
  onChange,
  height = 320,
  readOnly = false,
}: {
  value: string;
  onChange?: (v: string) => void;
  height?: number | string;
  readOnly?: boolean;
}) {
  configureMonacoOnce();
  const theme = useDarkTheme();
  return (
    <div className="overflow-hidden rounded-lg border border-brand-border dark:border-brand-700">
      <Editor
        height={height}
        language="rego"
        theme={theme}
        value={value}
        onChange={(v) => onChange?.(v ?? "")}
        options={{ ...COMMON, readOnly }}
      />
    </div>
  );
}

export function RegoDiff({
  original,
  modified,
  height = 320,
}: {
  original: string;
  modified: string;
  height?: number | string;
}) {
  configureMonacoOnce();
  const theme = useDarkTheme();
  return (
    <div className="overflow-hidden rounded-lg border border-brand-border dark:border-brand-700">
      <DiffEditor
        height={height}
        language="rego"
        theme={theme}
        original={original}
        modified={modified}
        options={{ ...COMMON, readOnly: true, renderSideBySide: true }}
      />
    </div>
  );
}
