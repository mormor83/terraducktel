"""Diff-engine interface for environment compares (Governance › Environments).

An engine turns two leaf directories (source + target, each ``{relative path:
text}``) into a :class:`ConfigDiff` — raw per-file diffs for display plus
semantic :class:`Hunk` s that promotion selects — and applies a selection of
hunks to the target text. Terraform is the only real engine in v1; Helm is a
stub behind the same interface (values / chart version later).

Classification of a hunk, strongest first:

- ``backend``   — backend / remote-state config. Never promoted, no toggle.
- ``protected`` — matches the link's protected rules (env-specific values).
                  Shown, excluded from promotion unless explicitly toggled.
- ``promotable``
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import asdict, dataclass, field
from typing import Optional, Protocol

MAX_FILE_BYTES = 200_000
MAX_UNIFIED_LINES = 2_000
MAX_VALUE_CHARS = 2_000

CATEGORIES = ("module", "inputs", "providers", "other")
BACKEND_REASON = "Backend / remote-state config is never promoted"


@dataclass
class Hunk:
    id: str
    file: str
    key: str
    category: str
    kind: str  # added | removed | changed
    source_value: Optional[str]
    target_value: Optional[str]
    classification: str  # promotable | protected | backend
    protected_reason: Optional[str]
    applicable: bool
    not_applicable_reason: Optional[str]
    source_lines: Optional[tuple[int, int]]
    target_lines: Optional[tuple[int, int]]
    level: str = "key"  # key | file
    # Key of the top-most container missing on one side. Hunks sharing a group
    # are applied together: adding (or removing) a whole block/object brings
    # every leaf under it along, so selecting any of them selects all.
    group: Optional[str] = None


@dataclass
class FileDiff:
    path: str
    status: str  # added | removed | modified | unchanged
    unified: str
    truncated: bool
    source_text: Optional[str]
    target_text: Optional[str]


@dataclass
class ConfigDiff:
    files: list[FileDiff] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        def fix(v):
            if isinstance(v, tuple):
                return list(v)
            if isinstance(v, dict):
                return {k: fix(x) for k, x in v.items()}
            if isinstance(v, list):
                return [fix(x) for x in v]
            return v

        return fix(asdict(self))


class DiffEngine(Protocol):
    name: str

    def config_diff(
        self, source_files: dict[str, str], target_files: dict[str, str], protected_rules: dict
    ) -> ConfigDiff: ...

    def apply_hunks(
        self,
        source_files: dict[str, str],
        target_files: dict[str, str],
        hunk_ids: list[str],
        protected_rules: dict,
    ) -> tuple[dict[str, Optional[str]], list[str]]:
        """Recompute the diff and apply the selected hunks to the target text.

        Returns ``({path: new text, or None to delete}, errors)`` with only the
        files that actually change. Unknown, non-applicable and backend hunk
        ids are reported as errors and never applied.
        """
        ...


# ─── keys ────────────────────────────────────────────────────────────────────

_IDX = re.compile(r"\[\d+\]$")


def join_key(segs) -> str:
    """Dotted display key; a segment containing a dot is double-quoted."""
    return ".".join(f'"{s}"' if "." in s else s for s in segs)


def split_key(key: str) -> list[str]:
    out, cur, q = [], [], False
    for c in key:
        if c == '"':
            q = not q
            continue
        if c == "." and not q:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(c)
    out.append("".join(cur))
    return out


# ─── protected rules ─────────────────────────────────────────────────────────

_GLOB_CHARS = set("*?[")


def _strip_quotes(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def protected_key_reason(key: str, rules: dict) -> Optional[str]:
    segs = [_IDX.sub("", s).lower() for s in split_key(key)]
    candidates = list(segs)
    if len(segs) >= 2:
        candidates.append(".".join(segs[-2:]))
    for pat in rules.get("keys") or []:
        p = pat.lower()
        if any(fnmatch.fnmatchcase(c, p) for c in candidates):
            return f"key matches '{pat}'"
    return None


def protected_value_reason(values: list[Optional[str]], rules: dict) -> Optional[str]:
    for raw in values:
        if raw is None:
            continue
        v = _strip_quotes(raw).lower()
        for pat in rules.get("values") or []:
            p = pat.lower()
            if not (_GLOB_CHARS & set(p)):
                if p and p in v:
                    return f"value contains '{pat}'"
            elif fnmatch.fnmatchcase(v, p) or any(
                fnmatch.fnmatchcase(tok, p) for tok in re.split(r"[\s\"'(),\[\]{}=]+", v) if tok
            ):
                return f"value matches '{pat}'"
    return None


def classify(key: str, values: list[Optional[str]], rules: dict, backend: bool) -> tuple[str, Optional[str]]:
    if backend:
        return "backend", None
    reason = protected_key_reason(key, rules) or protected_value_reason(values, rules)
    if reason:
        return "protected", reason
    return "promotable", None


def get_engine(kind: str) -> DiffEngine:
    if kind == "terraform":
        from app.services.envdiff.terraform import TerraformEngine

        return TerraformEngine()
    if kind == "helm":
        from app.services.envdiff.helm import HelmEngine

        return HelmEngine()
    raise ValueError(f"Unknown diff engine: {kind}")
