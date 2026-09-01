"""Key/value tags on workspaces: validation, normalization, matching.

Stored as a JSON object on `workspaces.tags` rather than a join table. At this
fleet size (low hundreds of workspaces per BU) a table plus a GIN index buys
nothing a dict scan doesn't already give, and it keeps tags atomic with the row
they describe — no partial write, no orphan cleanup on delete. If a deployment
ever grows to where `list_workspaces` can't hold the BU in memory, the upgrade
path is a `workspace_tags` table with the same public shape.

Keys are normalized to lowercase because `Team` and `team` being different tags
is a bug every tagging system learns the hard way. Values keep their case: they
are frequently display text.
"""
from __future__ import annotations

import re

# Deliberately narrow: tags end up in URLs, Slack messages and (later) cloud
# provider tags, all of which have their own opinions about punctuation.
_KEY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}[a-z0-9]|[a-z0-9]")
_VALUE_RE = re.compile(r"[\w .:/@+=-]{0,255}", re.UNICODE)

MAX_TAGS_PER_WORKSPACE = 32
KEY_MAX = 64
VALUE_MAX = 255


class TagError(ValueError):
    """Raised with a message intended for a 400 response."""


def normalize_key(key: str) -> str:
    return (key or "").strip().lower()


def validate(tags: dict) -> dict[str, str]:
    """Validate and normalize a whole tag map. Returns the cleaned copy."""
    if not isinstance(tags, dict):
        raise TagError("tags must be an object of key → value")
    if len(tags) > MAX_TAGS_PER_WORKSPACE:
        raise TagError(f"at most {MAX_TAGS_PER_WORKSPACE} tags per workspace")

    cleaned: dict[str, str] = {}
    for raw_key, raw_value in tags.items():
        key = normalize_key(str(raw_key))
        if not key:
            raise TagError("tag keys cannot be blank")
        if len(key) > KEY_MAX:
            raise TagError(f"tag key '{key[:16]}…' exceeds {KEY_MAX} characters")
        if not _KEY_RE.fullmatch(key):
            raise TagError(
                f"invalid tag key '{key}': use lowercase letters, digits, "
                "'.', '_' or '-', starting and ending alphanumeric"
            )
        if raw_value is None:
            value = ""
        elif isinstance(raw_value, (str, int, float, bool)):
            value = str(raw_value)
        else:
            raise TagError(f"tag '{key}' must have a scalar value")
        if len(value) > VALUE_MAX:
            raise TagError(f"tag '{key}' value exceeds {VALUE_MAX} characters")
        if not _VALUE_RE.fullmatch(value):
            raise TagError(f"tag '{key}' has an invalid value")
        # Normalizing the key can collide two distinct inputs ("Team"/"team").
        if key in cleaned:
            raise TagError(f"duplicate tag key '{key}' after normalization")
        cleaned[key] = value
    return cleaned


def parse_filter(expr: str) -> tuple[str, str | None]:
    """`team=payments` → ('team', 'payments'); `team` → ('team', None).

    A bare key matches any workspace carrying it, whatever the value — useful
    for "everything that has an owner set".
    """
    expr = (expr or "").strip()
    if not expr:
        raise TagError("empty tag filter")
    if "=" not in expr:
        return normalize_key(expr), None
    key, _, value = expr.partition("=")
    key = normalize_key(key)
    if not key:
        raise TagError(f"tag filter '{expr}' has no key")
    return key, value.strip()


def matches(tags: dict | None, filters: list[tuple[str, str | None]]) -> bool:
    """AND across filters — every one must hold."""
    have = tags or {}
    for key, value in filters:
        if key not in have:
            return False
        if value is not None and have[key] != value:
            return False
    return True


def apply_edit(
    current: dict | None,
    set_tags: dict | None = None,
    unset_keys: list[str] | None = None,
) -> dict[str, str]:
    """Merge a bulk edit onto one workspace's tags.

    `set` overwrites per key rather than replacing the whole map, so editing
    twenty workspaces' `team` tag can't wipe the `owner` tag one of them has.
    """
    merged = dict(current or {})
    for key in unset_keys or []:
        merged.pop(normalize_key(key), None)
    if set_tags:
        merged.update(validate(set_tags))
    return validate(merged)
