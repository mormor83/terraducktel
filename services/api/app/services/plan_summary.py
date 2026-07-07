"""Parse a stored `terraform show -json tfplan` payload into add/change/destroy
counts.

Used both by the auto-approve decision (was the plan 0/0/0?) and as a
single source of truth for future callers that only need the headline
numbers without building the full graph.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PlanSummary:
    add: int = 0
    change: int = 0
    destroy: int = 0
    # Resources whose action set is exactly {"no-op"} or {"read"} — surfaced
    # separately because they are NOT counted as changes for the purpose of
    # auto-approve.
    no_op: int = 0
    read: int = 0
    # True when add == change == destroy == 0. no-op / read counts are
    # ignored — they don't mutate state.
    @property
    def is_no_changes(self) -> bool:
        return self.add == 0 and self.change == 0 and self.destroy == 0


def _classify(actions: Iterable[str]) -> str:
    a = set(actions or [])
    if a == {"no-op"}:
        return "no_op"
    if a == {"read"}:
        return "read"
    if "create" in a and "delete" in a:
        # replace counts as both an add and a destroy in `terraform plan` summary
        # math; we keep one bucket here and the caller folds it into add+destroy.
        return "replace"
    if "create" in a:
        return "create"
    if "delete" in a:
        return "delete"
    if "update" in a:
        return "update"
    return "unknown"


def summarize_plan_json(plan_json: str | None) -> PlanSummary:
    """Parse `plan_json` and return a PlanSummary.

    On a missing / malformed / empty plan we return an all-zeros summary —
    the caller is responsible for deciding whether that should count as
    "no changes" or "indeterminate". For the auto-approve path the
    distinction doesn't matter: an empty plan_json means the executor
    didn't produce a structured plan, in which case we must NOT
    auto-approve. The caller checks `plan_json` truthiness explicitly.
    """
    if not plan_json:
        return PlanSummary()
    try:
        plan = json.loads(plan_json)
    except (ValueError, TypeError):
        return PlanSummary()
    # terraform show -json always yields an object; a non-dict payload (e.g. a
    # bare JSON array) is malformed for our purposes → treat as no structured
    # plan rather than raising AttributeError on `.get`.
    if not isinstance(plan, dict):
        return PlanSummary()

    add = change = destroy = no_op = read = 0
    for rc in plan.get("resource_changes", []) or []:
        c = (rc or {}).get("change", {}) or {}
        kind = _classify(c.get("actions") or [])
        if kind == "create":
            add += 1
        elif kind == "update":
            change += 1
        elif kind == "delete":
            destroy += 1
        elif kind == "replace":
            add += 1
            destroy += 1
        elif kind == "no_op":
            no_op += 1
        elif kind == "read":
            read += 1
    return PlanSummary(add=add, change=change, destroy=destroy, no_op=no_op, read=read)
