"""Terraform config diff engine.

Parses each side's HCL with python-hcl2's lark parser (``hcl2.parses_to_tree``)
and walks the tree itself, so every flattened key keeps the exact character
spans of its statement and value in the file. That is what makes promotion
*surgical*: a hunk is applied by splicing the target file's text at those
offsets, leaving every other byte (formatting, comments, ordering) alone. A
whole file is only ever rewritten by a file-level hunk (a file that exists on
one side only, a non-HCL file, or one that doesn't parse).

Flattening
    ``module "mon" { source = … }``  → ``module.mon.source``
    ``resource "aws_sg" "web" { ingress {…} ingress {…} }`` → ``…ingress[0].…``
    attribute objects recurse (``tags = { env = "dev" }`` → ``….tags.env``);
    lists / calls / expressions / heredocs are leaves compared on text
    normalised for comments and whitespace (so formatting and key order never
    produce hunks). ``locals`` blocks are merged.

Grouping
    When a whole block/object exists on one side only, every leaf under it
    shares ``group`` = the key of that top-most missing container, and applying
    any of them inserts (or deletes) the container once.

terraducktel.yaml
    Diffed as a YAML key tree. Only scalar ``changed`` hunks are applicable
    (located with a block-style indent-path scan); anything else is shown but
    marked not applicable.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Optional

import hcl2
import yaml
from lark import Token, Tree

from app.services.envdiff.base import (
    BACKEND_REASON,
    CATEGORIES,
    MAX_FILE_BYTES,
    MAX_UNIFIED_LINES,
    MAX_VALUE_CHARS,
    ConfigDiff,
    FileDiff,
    Hunk,
    classify,
    join_key,
    protected_value_reason,
)

Segs = tuple[str, ...]

_IGNORED_NAMES = {".terraform.tfstate.lock.info", "_terraducktel_backend.tf"}


def is_ignored(path: str) -> bool:
    parts = path.split("/")
    if ".terraform" in parts[:-1] or parts[0] == ".terraform":
        return True
    name = parts[-1]
    if name in _IGNORED_NAMES:
        return True
    if ".tfstate" in name or name.endswith(".tfplan") or name.startswith("tfplan"):
        return True
    return False


def _file_kind(path: str) -> str:
    name = posixpath.basename(path)
    if name == ".terraform.lock.hcl":
        return "lock"
    if name in ("terraducktel.yaml", "terraducktel.yml"):
        return "yaml"
    if name.endswith(".tfvars") and not name.endswith(".json"):
        return "tfvars"
    if name.endswith(".tf"):
        return "hcl"
    return "other"


# ─── text helpers ────────────────────────────────────────────────────────────


def _span(n) -> tuple[int, int]:
    if isinstance(n, Token):
        return n.start_pos, n.end_pos
    return n.meta.start_pos, n.meta.end_pos


def _trim(text: str, s: int, e: int) -> tuple[int, int]:
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _lines(text: str, span: tuple[int, int]) -> tuple[int, int]:
    s, e = span
    return _line_of(text, s), _line_of(text, max(s, e - 1))


def _line_start(text: str, pos: int) -> int:
    return text.rfind("\n", 0, pos) + 1


def _line_end(text: str, pos: int) -> int:
    j = text.find("\n", pos)
    return len(text) if j < 0 else j


def _indent_at(text: str, pos: int) -> str:
    ls = _line_start(text, pos)
    m = re.match(r"[ \t]*", text[ls:pos])
    return m.group(0) if m else ""


def _only_ws_before(text: str, pos: int) -> bool:
    return text[_line_start(text, pos):pos].strip() == ""


def _rest_is_trivia(rest: str) -> bool:
    r = rest.strip()
    if r.startswith(","):
        r = r[1:].strip()
    return r == "" or r.startswith("#") or r.startswith("//")


def normalize(value: str) -> str:
    """Comparison form of an HCL value: comments + insignificant whitespace out."""
    s = value.strip()
    if s.startswith("<<"):
        return "\n".join(line.rstrip() for line in s.split("\n"))
    out: list[str] = []
    i, n, in_str = 0, len(s), False
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "#" or s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c.isspace():
            i += 1
            continue
        out.append(c)
        i += 1
    return re.sub(r",(?=[\]\}\)])", "", "".join(out))


def _display(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    t = text.strip()
    return t if len(t) <= MAX_VALUE_CHARS else t[:MAX_VALUE_CHARS] + "…"


# ─── HCL flattening ──────────────────────────────────────────────────────────


@dataclass
class _Node:
    segs: Segs
    kind: str  # leaf | object | block | root
    stmt: tuple[int, int]  # whole statement (attribute / object elem / block)
    value: Optional[tuple[int, int]] = None  # attribute / elem value span
    norm: Optional[str] = None
    close: Optional[int] = None  # position of the container's closing brace
    open_end: Optional[int] = None
    child_stmts: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class _Parsed:
    text: str
    nodes: dict[Segs, _Node]

    def leaves(self) -> dict[Segs, _Node]:
        return {k: v for k, v in self.nodes.items() if v.kind == "leaf"}


def _ident(n) -> str:
    if isinstance(n, Token):
        return str(n.value)
    for c in n.children:
        if isinstance(c, Token):
            return str(c.value)
        return _ident(c)
    return ""


def _label(text: str, n) -> str:
    s, e = _span(n)
    raw = text[s:e]
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1]
    return raw


def _elem_key(text: str, key_node) -> str:
    inner = key_node.children[0] if key_node.children else key_node
    if isinstance(inner, Tree) and inner.data == "expr_term" and inner.children:
        inner = inner.children[0]
    if isinstance(inner, Tree) and inner.data == "identifier":
        return _ident(inner)
    return _label(text, inner)


def _object_of(expr) -> Optional[Tree]:
    if isinstance(expr, Tree) and expr.data == "expr_term" and len(expr.children) == 1:
        c = expr.children[0]
        if isinstance(c, Tree) and c.data == "object":
            return c
    return None


class _Walker:
    def __init__(self, text: str):
        self.text = text
        self.nodes: dict[Segs, _Node] = {}

    def run(self, tree: Tree) -> dict[Segs, _Node]:
        body = next((c for c in tree.children if isinstance(c, Tree) and c.data == "body"), None)
        root = _Node(segs=(), kind="root", stmt=(0, len(self.text)))
        self.nodes[()] = root
        if body is not None:
            self._body(body, (), root)
        return self.nodes

    def _block_base(self, blk: Tree, prefix: Segs) -> tuple[Segs, bool]:
        name = ""
        labels: list[str] = []
        for c in blk.children:
            if isinstance(c, Token):
                if c.type == "LBRACE":
                    break
                continue
            if c.data == "identifier" and not name:
                name = _ident(c)
            elif c.data in ("string", "identifier"):
                labels.append(_label(self.text, c))
        return prefix + (name, *labels), bool(labels)

    def _body(self, body: Tree, prefix: Segs, parent: _Node) -> None:
        blocks = [c for c in body.children if isinstance(c, Tree) and c.data == "block"]
        bases = [self._block_base(b, prefix) for b in blocks]
        counts: dict[Segs, int] = {}
        for segs, _ in bases:
            counts[segs] = counts.get(segs, 0) + 1
        seen: dict[Segs, int] = {}
        block_keys: dict[int, Segs] = {}
        for b, (segs, labeled) in zip(blocks, bases):
            i = seen.get(segs, 0)
            seen[segs] = i + 1
            if segs[-1:] == ("locals",) or counts[segs] == 1:
                key = segs
            elif labeled:
                key = segs if i == 0 else segs[:-1] + (f"{segs[-1]}[{i}]",)
            else:
                key = segs[:-1] + (f"{segs[-1]}[{i}]",)
            block_keys[id(b)] = key

        for c in body.children:
            if not isinstance(c, Tree):
                continue
            if c.data == "attribute":
                self._attribute(c, prefix, parent)
            elif c.data == "block":
                self._block(c, block_keys[id(c)], parent)

    def _block(self, blk: Tree, key: Segs, parent: _Node) -> None:
        lbrace = next(c for c in blk.children if isinstance(c, Token) and c.type == "LBRACE")
        rbrace = [c for c in blk.children if isinstance(c, Token) and c.type == "RBRACE"][-1]
        stmt = _trim(self.text, *_span(blk))
        parent.child_stmts.append(stmt)
        existing = self.nodes.get(key)
        if existing is not None and key[-1:] == ("locals",):
            node = existing  # merged locals blocks: keep the first as the insert anchor
        else:
            node = _Node(segs=key, kind="block", stmt=stmt, close=rbrace.start_pos, open_end=lbrace.end_pos)
            self.nodes[key] = node
        body = next((c for c in blk.children if isinstance(c, Tree) and c.data == "body"), None)
        if body is not None:
            self._body(body, key, node)

    def _attribute(self, attr: Tree, prefix: Segs, parent: _Node) -> None:
        name = _ident(attr.children[0])
        expr = attr.children[-1]
        self._keyed_value(prefix + (name,), attr, expr, parent)

    def _keyed_value(self, key: Segs, stmt_node, expr, parent: _Node) -> None:
        stmt = _trim(self.text, *_span(stmt_node))
        parent.child_stmts.append(stmt)
        value = _trim(self.text, *_span(expr))
        obj = _object_of(expr)
        if obj is not None:
            lbrace = obj.children[0]
            rbrace = obj.children[-1]
            node = _Node(segs=key, kind="object", stmt=stmt, value=value,
                         close=rbrace.start_pos, open_end=lbrace.end_pos)
            self.nodes[key] = node
            for c in obj.children:
                if isinstance(c, Tree) and c.data == "object_elem":
                    ek = _elem_key(self.text, c.children[0])
                    self._keyed_value(key + (ek,), c, c.children[-1], node)
            return
        raw = self.text[value[0]:value[1]]
        self.nodes[key] = _Node(segs=key, kind="leaf", stmt=stmt, value=value, norm=normalize(raw))


def parse_hcl(text: str) -> _Parsed:
    tree = hcl2.parses_to_tree(text)
    return _Parsed(text=text, nodes=_Walker(text).run(tree))


# ─── YAML (terraducktel.yaml) ────────────────────────────────────────────────

_YKEY = re.compile(r"""^(\s*)(?:"([^"]+)"|'([^']+)'|([^\s#"'\-][^:#]*?))\s*:(?:[ \t]+(.*))?$""")


def _yaml_flat(obj, prefix: Segs = ()) -> dict[Segs, object]:
    out: dict[Segs, object] = {}
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = {}
        for k, v in obj.items():
            out.update(_yaml_flat(v, prefix + (str(k),)))
    elif prefix:
        out[prefix] = obj
    return out


def _yaml_locate(text: str, path: Segs) -> Optional[tuple[int, int]]:
    """Char span of the scalar value at `path` in block-style YAML, or None."""
    stack: list[tuple[int, str]] = []
    offset = 0
    for line in text.split("\n"):
        start = offset
        offset += len(line) + 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        m = _YKEY.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        key = m.group(2) or m.group(3) or (m.group(4) or "").strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if tuple(k for _, k in stack) != path:
            continue
        rest = m.group(5)
        if not rest:
            return None
        vs = start + m.start(5)
        if rest[0] in "\"'":
            j = rest.find(rest[0], 1)
            val = rest[: j + 1] if j > 0 else rest
        else:
            k = rest.find(" #")
            val = (rest[:k] if k >= 0 else rest).rstrip()
        return vs, vs + len(val)
    return None


def _yaml_scalar(v) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _yaml_text(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


# ─── classification helpers ──────────────────────────────────────────────────


def _category(kind: str, segs: Segs) -> str:
    if kind == "lock":
        return "providers"
    if kind == "yaml":
        return "module" if segs[:2] == ("terraform", "version") else "other"
    if kind == "tfvars":
        return "inputs"
    head = segs[0] if segs else ""
    if head == "module":
        if len(segs) == 3 and segs[2] in ("source", "version"):
            return "module"
        return "inputs"
    if head == "terraform" and len(segs) > 1 and segs[1] in ("required_providers", "required_version"):
        return "providers"
    if head.startswith("provider"):
        return "providers"
    if head == "variable" and len(segs) >= 3 and segs[2] == "default":
        return "inputs"
    if head == "locals":
        return "inputs"
    return "other"


def _is_backend(path: str, kind: str, segs: Segs) -> bool:
    if posixpath.basename(path) == "backend.tf":
        return True
    if kind not in ("hcl",):
        return False
    if segs[:1] == ("terraform",) and len(segs) > 1 and segs[1] in ("backend", "cloud"):
        return True
    if segs[:2] == ("data", "terraform_remote_state"):
        return True
    return False


def _hunk_id(file: str, key: str, kind: str, sv: Optional[str], tv: Optional[str]) -> str:
    raw = "|".join([file, key, kind, sv or "", tv or ""])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


_REF = re.compile(r"[?&]ref=([^&\"\s]+)")


def _version_of(value: Optional[str], attr: str) -> Optional[str]:
    if value is None:
        return None
    v = value.strip().strip('"')
    if attr == "source":
        m = _REF.search(v)
        return m.group(1) if m else None
    return v


# ─── the engine ──────────────────────────────────────────────────────────────


@dataclass
class _Edit:
    start: int
    end: int
    text: str
    hunk: str


@dataclass
class _Internal:
    """A hunk plus what's needed to apply it (not serialised)."""

    hunk: Hunk
    edit: Optional[_Edit] = None
    whole_file: Optional[tuple[bool, Optional[str]]] = None  # (set, text|None)


class TerraformEngine:
    name = "terraform"

    # ── public API ──

    def config_diff(self, source_files: dict[str, str], target_files: dict[str, str],
                    protected_rules: dict) -> ConfigDiff:
        diff, _ = self._diff(source_files, target_files, protected_rules or {})
        return diff

    def apply_hunks(self, source_files: dict[str, str], target_files: dict[str, str],
                    hunk_ids: list[str], protected_rules: dict
                    ) -> tuple[dict[str, Optional[str]], list[str]]:
        _, internals = self._diff(source_files, target_files, protected_rules or {})
        by_id = {i.hunk.id: i for i in internals}
        errors: list[str] = []
        per_file: dict[str, list[_Edit]] = {}
        whole: dict[str, Optional[str]] = {}
        done_groups: set[tuple[str, str, str]] = set()

        for hid in dict.fromkeys(hunk_ids):
            it = by_id.get(hid)
            if it is None:
                errors.append(f"{hid}: unknown hunk (the compare may be stale)")
                continue
            h = it.hunk
            if h.classification == "backend":
                errors.append(f"{hid}: {h.file} {h.key}: {BACKEND_REASON}")
                continue
            if not h.applicable:
                errors.append(f"{hid}: {h.file} {h.key}: {h.not_applicable_reason or 'not applicable'}")
                continue
            if h.group:
                gk = (h.file, h.kind, h.group)
                if gk in done_groups:
                    continue
                done_groups.add(gk)
            if it.whole_file is not None:
                whole[h.file] = it.whole_file[1]
                continue
            if it.edit is not None:
                per_file.setdefault(h.file, []).append(it.edit)

        out: dict[str, Optional[str]] = {}
        for path, text in whole.items():
            if per_file.pop(path, None):
                errors.append(f"{path}: whole-file change selected together with key changes — applied the whole file")
            if text != target_files.get(path):
                out[path] = text

        for path, edits in per_file.items():
            original = target_files.get(path, "")
            new, errs = self._splice(original, edits)
            errors.extend(errs)
            if new == original:
                continue
            verr = self._verify(path, new)
            if verr:
                errors.append(f"{path}: result does not parse ({verr}) — not applied")
                continue
            out[path] = new
        return out, errors

    # ── internals ──

    @staticmethod
    def _splice(text: str, edits: list[_Edit]) -> tuple[str, list[str]]:
        errors: list[str] = []
        edits = sorted(edits, key=lambda e: (e.start, e.end), reverse=True)
        applied: list[_Edit] = []
        low = None
        for e in edits:
            if low is not None and e.end > low and not (e.start == e.end == low):
                errors.append(f"{e.hunk}: overlaps another selected change — not applied")
                continue
            text = text[: e.start] + e.text + text[e.end:]
            low = e.start
            applied.append(e)
        return text, errors

    @staticmethod
    def _verify(path: str, text: str) -> Optional[str]:
        kind = _file_kind(path)
        try:
            if kind in ("hcl", "tfvars", "lock"):
                hcl2.parses_to_tree(text)
            elif kind == "yaml":
                yaml.safe_load(text)
        except Exception as e:  # noqa: BLE001 — any parser error means "don't write it"
            return type(e).__name__
        return None

    def _diff(self, source_files: dict[str, str], target_files: dict[str, str], rules: dict
              ) -> tuple[ConfigDiff, list[_Internal]]:
        src = {p: t for p, t in source_files.items() if not is_ignored(p)}
        tgt = {p: t for p, t in target_files.items() if not is_ignored(p)}
        diff = ConfigDiff()
        internals: list[_Internal] = []

        for path in sorted(set(src) | set(tgt)):
            s, t = src.get(path), tgt.get(path)
            diff.files.append(self._file_diff(path, s, t))
            if s == t:
                continue
            kind = _file_kind(path)
            if s is None or t is None or kind == "other":
                internals.append(self._file_hunk(path, s, t, kind, rules))
                continue
            if kind == "yaml":
                got = self._yaml_hunks(path, s, t, rules, diff.warnings)
            else:
                got = self._hcl_hunks(path, s, t, kind, rules, diff.warnings)
            internals.extend(got)

        diff.hunks = [i.hunk for i in internals]
        diff.summary = self._summary(diff)
        return diff, internals

    @staticmethod
    def _file_diff(path: str, s: Optional[str], t: Optional[str]) -> FileDiff:
        if s == t:
            return FileDiff(path=path, status="unchanged", unified="", truncated=False,
                            source_text=None, target_text=None)
        status = "added" if t is None else "removed" if s is None else "modified"
        truncated = False

        def cap(x: Optional[str]) -> Optional[str]:
            nonlocal truncated
            if x is not None and len(x) > MAX_FILE_BYTES:
                truncated = True
                return x[:MAX_FILE_BYTES]
            return x

        s_c, t_c = cap(s), cap(t)
        lines = list(difflib.unified_diff(
            (t_c or "").splitlines(keepends=True), (s_c or "").splitlines(keepends=True),
            fromfile=f"target/{path}", tofile=f"source/{path}", n=3,
        ))
        if len(lines) > MAX_UNIFIED_LINES:
            lines = lines[:MAX_UNIFIED_LINES]
            truncated = True
        return FileDiff(path=path, status=status, unified="".join(lines), truncated=truncated,
                        source_text=s_c, target_text=t_c)

    @staticmethod
    def _file_hunk(path: str, s: Optional[str], t: Optional[str], kind: str, rules: dict,
                   ) -> _Internal:
        hkind = "added" if t is None else "removed" if s is None else "changed"
        backend = posixpath.basename(path) == "backend.tf"
        cls, reason = classify("<file>", [], rules, backend)
        if cls == "promotable":
            vr = protected_value_reason([s, t], rules)
            if vr:
                cls, reason = "protected", vr
        category = "providers" if kind == "lock" else "other"

        def nl(x: Optional[str]) -> Optional[tuple[int, int]]:
            return (1, max(1, x.count("\n") + (0 if x.endswith("\n") else 1))) if x else None

        h = Hunk(
            id=_hunk_id(path, "<file>", hkind, s, t),
            file=path, key="<file>", category=category, kind=hkind,
            source_value=_display(s), target_value=_display(t),
            classification=cls, protected_reason=reason,
            applicable=not backend, not_applicable_reason=BACKEND_REASON if backend else None,
            source_lines=nl(s), target_lines=nl(t), level="file",
        )
        return _Internal(hunk=h, whole_file=(True, s))

    def _mk(self, path: str, kind: str, segs: Segs, hkind: str, sv: Optional[str], tv: Optional[str],
            rules: dict, applicable: bool, why: Optional[str], sl, tl, group: Optional[Segs] = None,
            edit: Optional[_Edit] = None, norm_s: Optional[str] = None, norm_t: Optional[str] = None,
            ) -> _Internal:
        key = join_key(segs)
        backend = _is_backend(path, kind, segs)
        cls, reason = classify(key, [sv, tv], rules, backend)
        if backend:
            applicable, why = False, BACKEND_REASON
        h = Hunk(
            id=_hunk_id(path, key, hkind, norm_s if norm_s is not None else sv,
                        norm_t if norm_t is not None else tv),
            file=path, key=key, category=_category(kind, segs), kind=hkind,
            source_value=_display(sv), target_value=_display(tv),
            classification=cls, protected_reason=reason,
            applicable=applicable, not_applicable_reason=None if applicable else why,
            source_lines=sl, target_lines=tl, level="key",
            group=join_key(group) if group else None,
        )
        if edit is not None:
            edit.hunk = h.id
        return _Internal(hunk=h, edit=edit if applicable else None)

    # ── HCL ──

    def _hcl_hunks(self, path: str, s: str, t: str, kind: str, rules: dict, warnings: list[str]
                   ) -> list[_Internal]:
        try:
            sp = parse_hcl(s)
        except Exception:  # noqa: BLE001
            warnings.append(f"{path}: could not parse the source file — shown as a whole-file change")
            return [self._file_hunk(path, s, t, kind, rules)]
        try:
            tp = parse_hcl(t)
        except Exception:  # noqa: BLE001
            warnings.append(f"{path}: could not parse the target file — shown as a whole-file change")
            return [self._file_hunk(path, s, t, kind, rules)]

        out: list[_Internal] = []
        sn, tn = sp.nodes, tp.nodes

        # Keys that are a leaf on one side and a container on the other: one
        # whole-value change, and nothing below them is diffed separately.
        mismatch = {
            k for k in set(sn) & set(tn)
            if k and (sn[k].kind == "leaf") != (tn[k].kind == "leaf")
        }

        def under_mismatch(k: Segs) -> bool:
            return any(k[: len(m)] == m and k != m for m in mismatch)

        for k in sorted(mismatch):
            a, b = sn[k], tn[k]
            sv = s[a.value[0]:a.value[1]] if a.value else s[a.stmt[0]:a.stmt[1]]
            tv = t[b.value[0]:b.value[1]] if b.value else t[b.stmt[0]:b.stmt[1]]
            ok = a.value is not None and b.value is not None
            edit = _Edit(b.value[0], b.value[1], sv, "") if ok else None
            out.append(self._mk(path, kind, k, "changed", sv, tv, rules, ok,
                                "A block and an attribute share this key — edit it by hand",
                                _lines(s, a.stmt), _lines(t, b.stmt), edit=edit,
                                norm_s=normalize(sv), norm_t=normalize(tv)))

        s_leaves = {k: v for k, v in sn.items() if v.kind == "leaf" and not under_mismatch(k) and k not in mismatch}
        t_leaves = {k: v for k, v in tn.items() if v.kind == "leaf" and not under_mismatch(k) and k not in mismatch}

        # changed
        for k in sorted(set(s_leaves) & set(t_leaves)):
            a, b = s_leaves[k], t_leaves[k]
            if a.norm == b.norm:
                continue
            sv = s[a.value[0]:a.value[1]]
            tv = t[b.value[0]:b.value[1]]
            out.append(self._mk(path, kind, k, "changed", sv, tv, rules, True, None,
                                _lines(s, a.value), _lines(t, b.value),
                                edit=_Edit(b.value[0], b.value[1], sv, ""),
                                norm_s=a.norm, norm_t=b.norm))

        # added — leaves (and empty containers) present only in source
        def missing_top(k: Segs, own: dict, present: dict) -> Segs:
            """Top-most node on k's own side (an ancestor of k, or k) absent from `present`.

            Only real nodes count as ancestors — `module` alone is not a node,
            `module.mon` is (block keys include their labels).
            """
            for i in range(1, len(k) + 1):
                if k[:i] in own and k[:i] not in present:
                    return k[:i]
            return k

        def has_leaf_below(k: Segs, nodes: dict) -> bool:
            return any(x[: len(k)] == k and x != k and nodes[x].kind == "leaf" for x in nodes)

        added_keys = [k for k in s_leaves if k not in tn]
        added_keys += [k for k, v in sn.items()
                       if k and v.kind in ("block", "object") and k not in tn
                       and not has_leaf_below(k, sn) and not under_mismatch(k)]
        for k in sorted(set(added_keys)):
            top = missing_top(k, sn, tn)
            a = sn[k]
            sv_span = a.value or a.stmt
            sv = s[sv_span[0]:sv_span[1]]
            edit, why = self._insert_edit(sp, tp, top)
            out.append(self._mk(path, kind, k, "added", sv, None, rules, edit is not None, why,
                                _lines(s, sv_span), None,
                                group=top if top != k else None, edit=edit,
                                norm_s=a.norm if a.norm is not None else normalize(sv)))

        removed_keys = [k for k in t_leaves if k not in sn]
        removed_keys += [k for k, v in tn.items()
                         if k and v.kind in ("block", "object") and k not in sn
                         and not has_leaf_below(k, tn) and not under_mismatch(k)]
        for k in sorted(set(removed_keys)):
            top = missing_top(k, tn, sn)
            b = tn[k]
            tv_span = b.value or b.stmt
            tv = t[tv_span[0]:tv_span[1]]
            edit, why = self._delete_edit(tp, top)
            out.append(self._mk(path, kind, k, "removed", None, tv, rules, edit is not None, why,
                                None, _lines(t, tv_span),
                                group=top if top != k else None, edit=edit,
                                norm_t=b.norm if b.norm is not None else normalize(tv)))
        return out

    @staticmethod
    def _reindent(stmt_text: str, src_indent: str, new_indent: str) -> str:
        lines = stmt_text.split("\n")
        out = [lines[0]]
        for line in lines[1:]:
            if line.startswith(src_indent):
                out.append(new_indent + line[len(src_indent):])
            elif line.strip() == "":
                out.append(line)
            else:
                out.append(new_indent + line.lstrip())
        return "\n".join(out)

    def _insert_edit(self, sp: _Parsed, tp: _Parsed, top: Segs) -> tuple[Optional[_Edit], Optional[str]]:
        s, t = sp.text, tp.text
        node = sp.nodes[top]
        stmt_text = s[node.stmt[0]:node.stmt[1]]
        src_indent = _indent_at(s, node.stmt[0])
        # Nearest ancestor that is a node on both sides (the file root at worst).
        parent_key: Segs = ()
        for i in range(len(top) - 1, 0, -1):
            p = top[:i]
            if p in sp.nodes and p in tp.nodes:
                parent_key = p
                break
        parent = tp.nodes.get(parent_key)
        if parent is None or parent.kind == "leaf":
            return None, "The target has no place to insert this key"
        if parent.kind == "root":
            body = self._reindent(stmt_text, src_indent, "")
            sep = "" if t.endswith("\n") or t == "" else "\n"
            blank = "\n" if node.kind == "block" and t.strip() else ""
            return _Edit(len(t), len(t), f"{sep}{blank}{body}\n", ""), None
        close = parent.close
        if close is None or not _only_ws_before(t, close):
            return None, "The target block is written on one line — edit it by hand"
        if parent.child_stmts:
            indent = _indent_at(t, parent.child_stmts[0][0])
        else:
            indent = _indent_at(t, close) + "  "
        body = self._reindent(stmt_text, src_indent, indent)
        at = _line_start(t, close)
        return _Edit(at, at, f"{indent}{body}\n", ""), None

    @staticmethod
    def _delete_edit(tp: _Parsed, top: Segs) -> tuple[Optional[_Edit], Optional[str]]:
        t = tp.text
        node = tp.nodes[top]
        s0, e0 = node.stmt
        if not _only_ws_before(t, s0):
            return None, "Shares a line with other config — edit it by hand"
        le = _line_end(t, e0)
        if not _rest_is_trivia(t[e0:le]):
            return None, "Shares a line with other config — edit it by hand"
        start = _line_start(t, s0)
        end = min(len(t), le + 1)
        return _Edit(start, end, "", ""), None

    # ── YAML ──

    def _yaml_hunks(self, path: str, s: str, t: str, rules: dict, warnings: list[str]) -> list[_Internal]:
        try:
            so = yaml.safe_load(s) or {}
            to = yaml.safe_load(t) or {}
        except yaml.YAMLError:
            warnings.append(f"{path}: could not parse YAML — shown as a whole-file change")
            return [self._file_hunk(path, s, t, "yaml", rules)]
        if not isinstance(so, dict) or not isinstance(to, dict):
            return [self._file_hunk(path, s, t, "yaml", rules)]
        sf, tf = _yaml_flat(so), _yaml_flat(to)
        out: list[_Internal] = []
        for k in sorted(set(sf) | set(tf)):
            a, b = sf.get(k, _MISSING), tf.get(k, _MISSING)
            if a is not _MISSING and b is not _MISSING and _yaml_text(a) == _yaml_text(b):
                continue
            ss = _yaml_locate(s, k) if a is not _MISSING else None
            ts = _yaml_locate(t, k) if b is not _MISSING else None
            sv = s[ss[0]:ss[1]] if ss else (None if a is _MISSING else _yaml_text(a))
            tv = t[ts[0]:ts[1]] if ts else (None if b is _MISSING else _yaml_text(b))
            if a is _MISSING:
                hk, ok, why = "removed", False, "Remove it from terraducktel.yaml by hand"
            elif b is _MISSING:
                hk, ok, why = "added", False, "Add it to terraducktel.yaml by hand"
            else:
                hk = "changed"
                ok = _yaml_scalar(a) and _yaml_scalar(b) and ss is not None and ts is not None
                why = None if ok else "Only single-line scalar values can be applied in YAML"
            edit = _Edit(ts[0], ts[1], sv, "") if ok else None
            out.append(self._mk(path, "yaml", k, hk, sv, tv, rules, ok, why,
                                (_line_of(s, ss[0]),) * 2 if ss else None,
                                (_line_of(t, ts[0]),) * 2 if ts else None,
                                edit=edit,
                                norm_s=None if a is _MISSING else _yaml_text(a),
                                norm_t=None if b is _MISSING else _yaml_text(b)))
        return out

    # ── summary ──

    @staticmethod
    def _summary(diff: ConfigDiff) -> dict:
        cats = {c: 0 for c in CATEGORIES}
        module_versions: list[dict] = []
        providers: list[dict] = []
        for h in diff.hunks:
            cats[h.category] = cats.get(h.category, 0) + 1
            if h.kind != "changed" or h.level != "key":
                continue
            segs = h.key.split(".")
            if h.category == "module" and segs[0] == "module":
                attr = segs[-1]
                frm, to = _version_of(h.target_value, attr), _version_of(h.source_value, attr)
                if frm != to and (frm or to):
                    module_versions.append({"key": ".".join(segs[:-1]), "from": frm, "to": to})
            elif h.category == "module":
                module_versions.append({"key": h.key, "from": _strip(h.target_value), "to": _strip(h.source_value)})
            elif h.category == "providers" and (h.key.endswith(".version") or h.key.endswith("required_version")):
                providers.append({"key": h.key, "from": _strip(h.target_value), "to": _strip(h.source_value)})
        return {
            "files_changed": sum(1 for f in diff.files if f.status != "unchanged"),
            "keys_changed": sum(1 for h in diff.hunks if h.level == "key"),
            "protected_count": sum(1 for h in diff.hunks if h.classification == "protected"),
            "backend_count": sum(1 for h in diff.hunks if h.classification == "backend"),
            "promotable_count": sum(1 for h in diff.hunks if h.classification == "promotable"),
            "module_versions": module_versions,
            "providers": providers,
            "categories": cats,
        }


def _strip(v: Optional[str]) -> Optional[str]:
    return v.strip().strip('"') if v is not None else None


class _Missing:
    pass


_MISSING = _Missing()
