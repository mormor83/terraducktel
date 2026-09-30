"""Environment-link pairing engine — pure functions, no DB.

A link joins a *source node* to a *target node* of the synced repo tree
(`account-<id>` / `<region>` / `<folder…>` / `<stack>`). Pairing decides which
source stack corresponds to which target stack:

1. Each stack's **relative path** is its `tf_working_dir` minus the node's own
   path. Relative to the *node* (not just the `account-<id>/` prefix) so a
   folder link `…/monitoring` ↔ `…/observability` pairs its children without
   any rewrite rule; for account-level links the two are the same thing.
2. The link's ordered **rewrite rules** (`{from, to, regex}`) are applied to the
   source relative path.
3. Explicit **pair overrides** are matched first; **exclusions** drop a path
   from either side; everything else pairs on equal paths.

Helm stacks never pair in v1 (the diff engine is terraform-only) — they are
counted and reported, not silently dropped. A relative path that more than one
stack on the same side resolves to is **ambiguous**: it is reported and never
paired, because promoting into the wrong one of two same-path stacks is exactly
the mistake this screen exists to prevent.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

from app.services.repo_discovery import ACCOUNT_DIR_RE, REGION_DIR_RE

Level = Literal["account", "region", "folder", "stack"]
LEVELS: tuple[str, ...] = ("account", "region", "folder", "stack")

# Pair statuses. `not_compared` is the state a freshly matched pair sits in
# until the diff engine has looked at it; `in_sync` / `diverged` are only ever
# set by a compare.
PAIR_STATUSES: tuple[str, ...] = (
    "not_compared",
    "in_sync",
    "diverged",
    "missing_in_target",
    "missing_in_source",
    "excluded",
)

MAX_REWRITE_RULES = 20
MAX_PATTERN_LEN = 200

DEFAULT_PROTECTED_KEYS: tuple[str, ...] = (
    "*account_id*",
    "*arn*",
    "env",
    "environment",
    "instance_type",
    "instance_class",
    "*_count",
    "min_size",
    "max_size",
    "desired_capacity",
    "replicas",
    "*domain*",
    "*hosted_zone*",
    "*vpc_id*",
    "*subnet*",
    "*security_group*",
    "*kms*",
)


class PairingError(ValueError):
    """A link definition that can't be paired (bad node, bad rule…)."""


@dataclass(frozen=True)
class Stack:
    id: str
    tf_working_dir: str
    kind: str = "terraform"
    name: str = ""


@dataclass
class Pair:
    source_rel: Optional[str]
    target_rel: Optional[str]
    source_stack_id: Optional[str]
    target_stack_id: Optional[str]
    status: str
    reason: Optional[str] = None
    # missing_in_target only: where the stack would land if created there.
    proposed_target_rel: Optional[str] = None

    @property
    def key(self) -> str:
        """Stable identity of a pair within a link (survives re-pairing)."""
        return f"{self.source_rel if self.source_rel is not None else '-'}" \
               f"=>{self.target_rel if self.target_rel is not None else '-'}"

    @property
    def relative_path(self) -> str:
        """Display path — the target side when there is one."""
        if self.target_rel is not None:
            return self.target_rel
        return self.source_rel or ""


@dataclass
class PairingResult:
    pairs: list[Pair] = field(default_factory=list)
    helm_skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in PAIR_STATUSES}
        for p in self.pairs:
            out[p.status] = out.get(p.status, 0) + 1
        out["total"] = len(self.pairs)
        return out


# ─── paths & nodes ───────────────────────────────────────────────────────────


def norm_path(p: str | None) -> str:
    return (p or "").strip().strip("/")


def node_account_id(path: str) -> Optional[str]:
    """The AWS account id encoded in a node path's first segment, if any."""
    first = norm_path(path).split("/", 1)[0]
    m = ACCOUNT_DIR_RE.match(first)
    return m.group(1) if m else None


def under(path: str, node_path: str) -> bool:
    path, node_path = norm_path(path), norm_path(node_path)
    return path == node_path or path.startswith(node_path + "/")


def relative_to(path: str, node_path: str) -> str:
    path, node_path = norm_path(path), norm_path(node_path)
    if path == node_path:
        return ""
    return path[len(node_path) + 1:]


def stacks_under(node_path: str, stacks: Iterable[Stack]) -> list[Stack]:
    return [s for s in stacks if under(s.tf_working_dir, node_path)]


def infer_level(node_path: str, stacks: Iterable[Stack]) -> Level:
    """Which tree level a node path sits at, judged against the real stacks."""
    path = norm_path(node_path)
    if not path:
        raise PairingError("Node path is empty")
    if any(norm_path(s.tf_working_dir) == path for s in stacks):
        return "stack"
    segs = path.split("/")
    if len(segs) == 1 and ACCOUNT_DIR_RE.match(segs[0]):
        return "account"
    if len(segs) == 2 and ACCOUNT_DIR_RE.match(segs[0]) and REGION_DIR_RE.match(segs[1]):
        return "region"
    return "folder"


def validate_nodes(
    source_path: str,
    target_path: str,
    stacks: list[Stack],
    source_level: Optional[str] = None,
    target_level: Optional[str] = None,
) -> Level:
    """Check a source/target node pair is linkable; return their shared level."""
    src, tgt = norm_path(source_path), norm_path(target_path)
    if not src or not tgt:
        raise PairingError("Both a source and a target node are required")
    if under(src, tgt) or under(tgt, src):
        raise PairingError("Source and target overlap — pick two disjoint nodes")

    levels = []
    for side, path, claimed in (("source", src, source_level), ("target", tgt, target_level)):
        side_stacks = stacks_under(path, stacks)
        if not side_stacks:
            raise PairingError(f"The {side} node has no stacks under it")
        if not any(s.kind == "terraform" for s in side_stacks):
            raise PairingError(
                f"The {side} node only contains Helm stacks — Helm environments are coming soon"
            )
        level = infer_level(path, stacks)
        if claimed is not None and claimed != level:
            raise PairingError(f"The {side} node is a {level}, not a {claimed}")
        levels.append(level)
    if levels[0] != levels[1]:
        raise PairingError(
            f"Both sides must be at the same level (source is a {levels[0]}, target is a {levels[1]})"
        )
    return levels[0]  # type: ignore[return-value]


# ─── rules ───────────────────────────────────────────────────────────────────


def validate_rewrite_rules(rules: list[dict]) -> list[dict]:
    """Normalise + validate `[{from, to, regex}]`; compile-check regexes."""
    if len(rules) > MAX_REWRITE_RULES:
        raise PairingError(f"At most {MAX_REWRITE_RULES} rewrite rules per link")
    out = []
    for i, r in enumerate(rules):
        frm = str(r.get("from") or "")
        to = str(r.get("to") or "")
        regex = bool(r.get("regex", False))
        if not frm:
            raise PairingError(f"Rewrite rule #{i + 1} has an empty 'from'")
        if len(frm) > MAX_PATTERN_LEN or len(to) > MAX_PATTERN_LEN:
            raise PairingError(f"Rewrite rule #{i + 1} is too long")
        if regex:
            try:
                compiled = re.compile(frm)
                compiled.sub(to, "")  # validates back-references in `to`
            except (re.error, IndexError) as e:
                raise PairingError(f"Rewrite rule #{i + 1} is not a valid regex: {e}") from None
        out.append({"from": frm, "to": to, "regex": regex})
    return out


def apply_rewrites(rel: str, rules: list[dict]) -> str:
    for r in rules:
        if r.get("regex"):
            rel = re.sub(r["from"], r["to"], rel)
        else:
            rel = rel.replace(r["from"], r["to"])
    return norm_path(rel)


def validate_overrides(overrides: dict | None) -> dict:
    overrides = overrides or {}
    pairs = []
    for p in overrides.get("pairs") or []:
        if not isinstance(p, dict) or "source" not in p or "target" not in p:
            raise PairingError("Each pair override needs 'source' and 'target'")
        pairs.append({"source": norm_path(p["source"]), "target": norm_path(p["target"])})
    exclude = []
    for e in overrides.get("exclude") or []:
        if not isinstance(e, dict) or e.get("side") not in ("source", "target"):
            raise PairingError("Each exclusion needs side='source'|'target' and a path")
        exclude.append({"side": e["side"], "path": norm_path(e.get("path"))})
    return {"pairs": pairs, "exclude": exclude}


def default_protected_rules(source_account: Optional[str], target_account: Optional[str]) -> dict:
    """Seed rules for a new link: env-specific keys, plus both account ids.

    Value patterns are case-insensitive globs matched against a whole scalar
    value; the ARN patterns catch any ARN that embeds either account.
    """
    values: list[str] = []
    for acct in (source_account, target_account):
        if acct and acct not in values:
            values.append(acct)
    for acct in (source_account, target_account):
        if acct:
            values.append(f"arn:*:{acct}:*")
    return {"keys": list(DEFAULT_PROTECTED_KEYS), "values": values}


def validate_protected_rules(rules: dict | None) -> dict:
    rules = rules or {}
    keys = [str(k).strip() for k in rules.get("keys") or [] if str(k).strip()]
    values = [str(v).strip() for v in rules.get("values") or [] if str(v).strip()]
    for pat in keys + values:
        if len(pat) > MAX_PATTERN_LEN:
            raise PairingError("Protected-rule pattern is too long")
    return {"keys": keys, "values": values}


def is_protected_key(dotted_key: str, rules: dict) -> bool:
    """Does any segment of a dotted key (or the whole key) match a key glob?"""
    k = dotted_key.lower()
    candidates = [k, *k.split(".")]
    return any(
        fnmatch.fnmatchcase(c, pat.lower()) for pat in rules.get("keys") or [] for c in candidates
    )


def is_protected_value(value: str, rules: dict) -> bool:
    v = str(value).lower()
    return any(fnmatch.fnmatchcase(v, pat.lower()) for pat in rules.get("values") or [])


# ─── pairing ─────────────────────────────────────────────────────────────────


def _index(stacks: list[Stack], rel_of) -> tuple[dict[str, Stack], set[str]]:
    by_rel: dict[str, Stack] = {}
    dup: set[str] = set()
    for s in stacks:
        rel = rel_of(s)
        if rel in by_rel:
            dup.add(rel)
        else:
            by_rel[rel] = s
    for rel in dup:
        by_rel.pop(rel, None)
    return by_rel, dup


def pair_stacks(
    source_path: str,
    target_path: str,
    stacks: list[Stack],
    rewrite_rules: list[dict] | None = None,
    overrides: dict | None = None,
) -> PairingResult:
    """Pair the terraform stacks under two nodes. Deterministic ordering."""
    rules = rewrite_rules or []
    ov = validate_overrides(overrides)
    result = PairingResult()

    src_all = stacks_under(source_path, stacks)
    tgt_all = stacks_under(target_path, stacks)
    result.helm_skipped = sum(1 for s in src_all + tgt_all if s.kind != "terraform")
    src_tf = [s for s in src_all if s.kind == "terraform"]
    tgt_tf = [s for s in tgt_all if s.kind == "terraform"]

    # Source stacks keep their *raw* relative path (what pair overrides and
    # exclusions refer to) plus the rewritten one they match on.
    src_raw = {s.id: relative_to(s.tf_working_dir, source_path) for s in src_tf}
    src_by_raw, src_dup = _index(src_tf, lambda s: src_raw[s.id])
    tgt_by_rel, tgt_dup = _index(tgt_tf, lambda s: relative_to(s.tf_working_dir, target_path))
    for rel in sorted(src_dup):
        result.warnings.append(f"Ambiguous: several source stacks at '{rel or '.'}' — not paired")
    for rel in sorted(tgt_dup):
        result.warnings.append(f"Ambiguous: several target stacks at '{rel or '.'}' — not paired")

    excluded_src = {e["path"] for e in ov["exclude"] if e["side"] == "source"}
    excluded_tgt = {e["path"] for e in ov["exclude"] if e["side"] == "target"}
    used_src: set[str] = set()
    used_tgt: set[str] = set()

    # 1. Explicit pair overrides.
    for o in ov["pairs"]:
        s = src_by_raw.get(o["source"])
        t = tgt_by_rel.get(o["target"])
        if s is None or t is None:
            missing = "source" if s is None else "target"
            result.warnings.append(
                f"Pair override {o['source'] or '.'} → {o['target'] or '.'}: "
                f"no {missing} stack at that path"
            )
            continue
        if o["source"] in used_src or o["target"] in used_tgt:
            result.warnings.append(
                f"Pair override {o['source'] or '.'} → {o['target'] or '.'} reuses a stack — ignored"
            )
            continue
        used_src.add(o["source"])
        used_tgt.add(o["target"])
        result.pairs.append(Pair(o["source"], o["target"], s.id, t.id, "not_compared", "override"))

    # 2. Exclusions.
    for raw in sorted(excluded_src):
        s = src_by_raw.get(raw)
        if s is not None and raw not in used_src:
            used_src.add(raw)
            result.pairs.append(Pair(raw, None, s.id, None, "excluded", "excluded (source)"))
    for rel in sorted(excluded_tgt):
        t = tgt_by_rel.get(rel)
        if t is not None and rel not in used_tgt:
            used_tgt.add(rel)
            result.pairs.append(Pair(None, rel, None, t.id, "excluded", "excluded (target)"))

    # 3. Path matching on the rewritten source path. Two sources rewriting to
    # the same target path is the same ambiguity as a duplicate — refuse both.
    rewritten: dict[str, list[str]] = {}
    for raw in sorted(src_by_raw):
        if raw in used_src:
            continue
        rewritten.setdefault(apply_rewrites(raw, rules), []).append(raw)

    for rel, raws in sorted(rewritten.items()):
        if len(raws) > 1:
            result.warnings.append(
                f"Ambiguous: {', '.join(r or '.' for r in raws)} all rewrite to '{rel or '.'}' — not paired"
            )
            for raw in raws:
                used_src.add(raw)
                result.pairs.append(
                    Pair(raw, None, src_by_raw[raw].id, None, "excluded", "ambiguous rewrite")
                )
            continue
        raw = raws[0]
        used_src.add(raw)
        t = tgt_by_rel.get(rel)
        if t is not None and rel not in used_tgt:
            used_tgt.add(rel)
            result.pairs.append(Pair(raw, rel, src_by_raw[raw].id, t.id, "not_compared"))
        else:
            # Where the stack *would* land in the target (used by create-in-target).
            result.pairs.append(
                Pair(raw, None, src_by_raw[raw].id, None, "missing_in_target",
                     proposed_target_rel=rel)
            )

    for rel in sorted(tgt_by_rel):
        if rel not in used_tgt:
            result.pairs.append(Pair(None, rel, None, tgt_by_rel[rel].id, "missing_in_source"))

    result.pairs.sort(key=lambda p: (p.relative_path, p.key))
    return result
