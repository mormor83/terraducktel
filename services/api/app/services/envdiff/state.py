"""Live-state comparison for environment links — pure, no I/O.

Turns a Terraform state (v4 JSON, as the API's state store already holds it)
into a redacted resource inventory, and diffs two inventories: counts by type,
resources only on one side, and resources on both sides whose attributes
differ.

Two things are load-bearing here:

* **Redaction happens at parse time.** Every attribute Terraform lists in an
  instance's ``sensitive_attributes``, and every key whose name looks like a
  secret (``SECRET_KEY_GLOBS``), is replaced by ``REDACTED`` for its whole
  subtree before anything else sees it. Nothing downstream — the diff, the
  cache, the API response, a log line — can leak a value that never made it
  into the inventory.
* **Noise is dropped at parse time too.** Identity / computed / timestamp
  attributes (``NOISE_KEYS``) differ between any two environments by
  construction; keeping them would make every resource read as "differing".

Values are compared as rendered strings. The caller's ``normalize`` hook is
applied to *source* values before comparing, so a Dev ARN that differs from
its Prod twin only by account id (and region, via rewrite rules) compares
equal.
"""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Optional

REDACTED = "(redacted)"

# Matched (case-insensitive fnmatch) against each *attribute-name* segment of a
# flattened key; a match drops that whole subtree. Limited to identity,
# computed and timestamp attributes — meaningful config such as `name`,
# `instance_type` or `engine_version` must never land here.
NOISE_KEYS: frozenset[str] = frozenset({
    "id",
    "arn",
    "tags_all",
    "owner_id",
    "unique_id",
    "etag",
    "*_at",
    "created",
    "created_time",
    "create_time",
    "created_date",
    "creation_date",
    "creation_time",
    "last_modified*",
    "*_date",
    "timeouts",
    "version_id",
    "latest_version",
    "hosted_zone_id_computed",
})

SECRET_KEY_GLOBS: tuple[str, ...] = ("*password*", "*secret*", "*token*", "*private_key*")

# Display caps (comparison always uses the full value).
MAX_VALUE_LEN = 1000

_IDENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class StateMeta:
    serial: Optional[int] = None
    lineage: Optional[str] = None
    terraform_version: Optional[str] = None
    resource_count: int = 0


@dataclass
class Resource:
    address: str
    type: str
    module: str
    # Flattened dotted keys ("tags.env", "ingress[0].from_port",
    # 'tags["kubernetes.io/role"]') → rendered value; secrets are REDACTED.
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class Inventory:
    meta: StateMeta = field(default_factory=StateMeta)
    resources: dict[str, Resource] = field(default_factory=dict)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _matches(name: str, globs: Iterable[str]) -> bool:
    n = name.lower()
    return any(fnmatch.fnmatchcase(n, g.lower()) for g in globs)


def _is_secret_key(name: str) -> bool:
    return _matches(name, SECRET_KEY_GLOBS)


def _is_noise_key(name: str) -> bool:
    return _matches(name, NOISE_KEYS)


def _render(v: Any) -> str:
    if isinstance(v, str):
        return v
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def _join(prefix: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{prefix}[{key}]"
    key = str(key)
    if _IDENT_RE.match(key):
        return f"{prefix}.{key}" if prefix else key
    return f"{prefix}[{json.dumps(key)}]"


def _index_key(k: Any) -> str:
    if k is None:
        return ""
    if isinstance(k, bool):  # bool is an int subclass; terraform never uses it
        return f"[{json.dumps(k)}]"
    if isinstance(k, int):
        return f"[{k}]"
    return f"[{json.dumps(str(k))}]"


def _sensitive_paths(raw: Any) -> set[tuple]:
    """v4 `sensitive_attributes` → a set of path tuples (str keys, int indexes)."""
    out: set[tuple] = set()
    if not isinstance(raw, list):
        return out
    for path in raw:
        if not isinstance(path, list):
            continue
        segs: list = []
        ok = True
        for step in path:
            if not isinstance(step, dict):
                ok = False
                break
            kind = step.get("type")
            val = step.get("value")
            if kind == "get_attr":
                segs.append(str(val))
            elif kind == "index":
                inner = val.get("value") if isinstance(val, dict) else val
                vtype = val.get("type") if isinstance(val, dict) else None
                if vtype == "number" or (isinstance(inner, int) and not isinstance(inner, bool)):
                    try:
                        segs.append(int(inner))
                    except (TypeError, ValueError):
                        ok = False
                        break
                else:
                    segs.append(str(inner))
            else:
                ok = False
                break
        if ok and segs:
            out.add(tuple(segs))
    return out


def _flatten(
    value: Any,
    path: tuple,
    key: str,
    sensitive: set[tuple],
    out: dict[str, str],
) -> None:
    if path and path in sensitive:
        out[key] = REDACTED
        return
    if isinstance(value, dict):
        if not value:
            if key:
                out[key] = "{}"
            return
        for k in sorted(value, key=str):
            name = str(k)
            child_key = _join(key, name)
            if _is_secret_key(name):
                out[child_key] = REDACTED
                continue
            # Noise applies to attribute names only, not to user map keys
            # (a tag literally named "id" is data, not identity).
            if _IDENT_RE.match(name) and _is_noise_key(name):
                continue
            _flatten(value[k], path + (name,), child_key, sensitive, out)
        return
    if isinstance(value, list):
        if not value:
            if key:
                out[key] = "[]"
            return
        for i, item in enumerate(value):
            _flatten(item, path + (i,), _join(key, i), sensitive, out)
        return
    if key:
        out[key] = _render(value)


def _load(raw: bytes | str | dict | None) -> Optional[dict]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid Terraform state JSON: {e.msg}") from None
    if not isinstance(data, dict):
        raise ValueError("Invalid Terraform state: not a JSON object")
    return data


# ─── public API ──────────────────────────────────────────────────────────────


def parse_state(raw: bytes | str | dict | None) -> Inventory:
    """Parse a v4 tfstate into a redacted, noise-free managed-resource inventory."""
    data = _load(raw)
    if not data:
        return Inventory()

    resources: dict[str, Resource] = {}
    for res in data.get("resources") or []:
        if not isinstance(res, dict) or res.get("mode") != "managed":
            continue
        rtype = str(res.get("type") or "")
        rname = str(res.get("name") or "")
        module = str(res.get("module") or "")
        base = f"{module}.{rtype}.{rname}" if module else f"{rtype}.{rname}"
        for inst in res.get("instances") or []:
            if not isinstance(inst, dict):
                continue
            address = base + _index_key(inst.get("index_key"))
            attrs: dict[str, str] = {}
            _flatten(
                inst.get("attributes") or {},
                (),
                "",
                _sensitive_paths(inst.get("sensitive_attributes")),
                attrs,
            )
            resources[address] = Resource(address=address, type=rtype, module=module, attributes=attrs)

    serial = data.get("serial")
    return Inventory(
        meta=StateMeta(
            serial=serial if isinstance(serial, int) else None,
            lineage=data.get("lineage") if isinstance(data.get("lineage"), str) else None,
            terraform_version=(
                data.get("terraform_version") if isinstance(data.get("terraform_version"), str) else None
            ),
            resource_count=len(resources),
        ),
        resources=resources,
    )


def normalizer(pairs: list[tuple[str, str]]) -> Callable[[str], str]:
    """Plain, ordered substring replacements (e.g. source → target account id)."""
    active = [(a, b) for a, b in pairs if a]

    def _norm(v: str) -> str:
        for a, b in active:
            v = v.replace(a, b)
        return v

    return _norm


def _display(v: Optional[str]) -> Optional[str]:
    if v is None or len(v) <= MAX_VALUE_LEN:
        return v
    return v[:MAX_VALUE_LEN] + "…"


def state_diff(
    source: Inventory,
    target: Inventory,
    *,
    normalize: Optional[Callable[[str], str]] = None,
    max_resources: int = 500,
    max_attrs: int = 50,
) -> dict:
    """Diff two inventories. JSON-serialisable; values are already redacted."""
    src, tgt = source.resources, target.resources

    types = sorted({r.type for r in src.values()} | {r.type for r in tgt.values()})
    src_counts: dict[str, int] = {}
    tgt_counts: dict[str, int] = {}
    for r in src.values():
        src_counts[r.type] = src_counts.get(r.type, 0) + 1
    for r in tgt.values():
        tgt_counts[r.type] = tgt_counts.get(r.type, 0) + 1

    only_src = sorted(set(src) - set(tgt))
    only_tgt = sorted(set(tgt) - set(src))
    both = sorted(set(src) & set(tgt))

    differing: list[dict] = []
    for addr in both:
        s_attrs, t_attrs = src[addr].attributes, tgt[addr].attributes
        diffs: list[dict] = []
        for key in sorted(set(s_attrs) | set(t_attrs)):
            s = s_attrs.get(key)
            t = t_attrs.get(key)
            if s is not None and s != REDACTED and normalize is not None:
                s = normalize(s)
            if s == t:
                continue
            diffs.append({"key": key, "source": _display(s), "target": _display(t)})
        if diffs:
            differing.append({
                "address": addr,
                "type": src[addr].type,
                "attributes": diffs[:max_attrs],
                "attrs_truncated": len(diffs) > max_attrs,
            })

    return {
        "source_meta": asdict(source.meta),
        "target_meta": asdict(target.meta),
        "type_counts": [
            {"type": t, "source": src_counts.get(t, 0), "target": tgt_counts.get(t, 0)} for t in types
        ],
        "only_in_source": only_src[:max_resources],
        "only_in_target": only_tgt[:max_resources],
        "truncated": len(only_src) > max_resources or len(only_tgt) > max_resources
        or len(differing) > max_resources,
        "differing": differing[:max_resources],
        "in_both": len(both),
        "identical": len(both) - len(differing),
    }
