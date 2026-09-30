"""Helpers for "Create in target": copying a source leaf to a new target path.

A copied leaf must not carry the source's backend: a hard-coded
`backend "s3" { key = … }` would point the new stack at the *source* stack's
state. TDT bootstraps new stacks by letting the executor inject its own HTTP
backend when the leaf declares none, so the copy simply drops any
`terraform { backend … }` / `terraform { cloud … }` block (by exact lark span,
leaving everything else byte-for-byte).
"""
from __future__ import annotations

import hcl2
from lark import Token, Tree


def _ident(n) -> str:
    for c in n.children:
        if isinstance(c, Tree) and c.data == "identifier":
            tok = c.children[0]
            return str(tok)
        if isinstance(c, Token) and c.type == "NAME":
            return str(c)
    return ""


def _blocks(body: Tree):
    for c in body.children:
        if isinstance(c, Tree) and c.data == "block":
            yield c


def _body_of(block: Tree) -> Tree | None:
    for c in block.children:
        if isinstance(c, Tree) and c.data == "body":
            return c
    return None


def strip_backend(text: str) -> tuple[str, bool]:
    """Remove backend/cloud blocks inside `terraform {}`. Returns (text, removed?)."""
    try:
        tree = hcl2.parses_to_tree(text)
    except Exception:  # noqa: BLE001 — unparseable file is copied untouched
        return text, False
    spans: list[tuple[int, int]] = []
    top = next((c for c in tree.children if isinstance(c, Tree) and c.data == "body"), None)
    if top is None:
        return text, False
    for blk in _blocks(top):
        if _ident(blk) != "terraform":
            continue
        body = _body_of(blk)
        if body is None:
            continue
        for inner in _blocks(body):
            if _ident(inner) in ("backend", "cloud") and not inner.meta.empty:
                start = text.rfind("\n", 0, inner.meta.start_pos) + 1
                end = text.find("\n", inner.meta.end_pos)
                end = len(text) if end == -1 else end + 1
                spans.append((start, end))
    if not spans:
        return text, False
    for s, e in sorted(spans, reverse=True):
        text = text[:s] + text[e:]
    return text, True
