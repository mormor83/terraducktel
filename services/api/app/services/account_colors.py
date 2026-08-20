"""Canonical colour palette for cloud accounts (AWS / Azure / GCP / K8s).

Why a fixed palette instead of a free-form hex picker:

  * The UI is theme-aware. A user-picked hex is illegible in one of the two
    themes; a named token maps to a curated light/dark Tailwind pair (see
    `services/ui/src/components/accountColors.ts`).
  * `CLAUDE.md` forbids hard-coded colours in the frontend — tokens keep the
    Tailwind-token rule intact.
  * Slack needs a hex (attachment stripe) *and* an emoji (fallback text, where
    attachments don't render). Slack only ships ~8 coloured-circle emoji, so the
    palette is sized to 8 to keep UI ↔ Slack strictly 1:1.

The TS side mirrors ORDER and token names; it does NOT reimplement the hash —
the API always returns a resolved token so there is exactly one implementation
of the fallback. Keep the two files in sync when adding a colour.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Optional

# Ordered — the derived-default hash indexes into this tuple, so REORDERING
# silently repaints every account that hasn't set an explicit colour. Append
# only.
ACCOUNT_COLORS: tuple[str, ...] = (
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "brown",
    "gray",
)

# Slack attachment `color` — the vertical stripe on the left of a message.
# Mid-tone hexes chosen to stay legible on both Slack themes.
COLOR_HEX: dict[str, str] = {
    "red": "#dc2626",
    "orange": "#ea580c",
    "yellow": "#ca8a04",
    "green": "#059669",
    "blue": "#2563eb",
    "purple": "#7c3aed",
    "brown": "#92400e",
    "gray": "#64748b",
}

# Prefixed onto the Slack fallback `text` so mobile pushes and notification
# previews — which render neither attachments nor blocks — still carry the cue.
COLOR_EMOJI: dict[str, str] = {
    "red": "🔴",
    "orange": "🟠",
    "yellow": "🟡",
    "green": "🟢",
    "blue": "🔵",
    "purple": "🟣",
    "brown": "🟤",
    "gray": "⚪",
}


class InvalidAccountColor(ValueError):
    """Raised for a colour token outside ACCOUNT_COLORS."""


def normalize(value: Optional[str]) -> Optional[str]:
    """Validate an incoming colour token from the API.

    `None` and `""` both mean "auto" (fall back to the derived default) and
    normalize to None so the column stores NULL rather than an empty string.
    """
    if value is None:
        return None
    v = value.strip().lower()
    if not v:
        return None
    if v not in COLOR_HEX:
        raise InvalidAccountColor(
            f"color must be one of {', '.join(ACCOUNT_COLORS)} (got {value!r})"
        )
    return v


def derive(key: str) -> str:
    """Last-resort colour for a row with a NULL `color`.

    Normally unreachable: the API assigns a distinct colour at creation (see
    `pick_next`) and migration 041 backfilled every pre-existing row. This
    covers rows inserted outside the API — seed scripts, direct SQL, a restored
    dump.

    sha256 rather than `hash()` because PYTHONHASHSEED randomizes str hashing
    per process, which would repaint the Runs page on every API restart. Note
    this can collide across accounts (8 buckets); `pick_next` is what actually
    guarantees distinctness.
    """
    digest = hashlib.sha256((key or "").encode("utf-8")).digest()
    return ACCOUNT_COLORS[int.from_bytes(digest[:4], "big") % len(ACCOUNT_COLORS)]


def pick_next(taken: Iterable[Optional[str]]) -> str:
    """First unused palette colour, so a new account never matches an existing
    one while there are still free colours.

    Past 8 accounts in a BU some reuse is unavoidable — then pick the
    least-used colour (ties broken by palette order) so collisions spread
    evenly instead of piling onto "red".
    """
    counts = {c: 0 for c in ACCOUNT_COLORS}
    for t in taken:
        if t in counts:
            counts[t] += 1
    return min(ACCOUNT_COLORS, key=lambda c: (counts[c], ACCOUNT_COLORS.index(c)))


async def used_colors_for_bu(session, business_unit_id: Optional[str]) -> list[str]:
    """Every colour already claimed by any cloud account in this BU.

    Deliberately spans all four provider tables: the Runs page interleaves
    Terraform (AWS/Azure/GCP) and Helm (K8s cluster) runs, so a colour reused
    across providers is just as confusing as one reused within a provider.

    Imports are local to keep this module import-safe from the schema layer.
    """
    from sqlalchemy import select

    from app.models.aws_account import AwsAccount
    from app.models.azure_subscription import AzureSubscription
    from app.models.gcp_project import GcpProject
    from app.models.k8s_cluster import K8sCluster

    out: list[str] = []
    for model in (AwsAccount, AzureSubscription, GcpProject, K8sCluster):
        stmt = select(model.color).where(model.color.is_not(None))
        if business_unit_id is not None:
            stmt = stmt.where(model.business_unit_id == business_unit_id)
        out.extend(c for c in (await session.execute(stmt)).scalars().all() if c)
    return out


async def assign_for_bu(session, business_unit_id: Optional[str], explicit: Optional[str]) -> str:
    """Resolve the colour to persist on a newly-created cloud account.

    An explicit choice always wins; otherwise claim the first free colour in
    the BU. Called on create so the value is stored once and never shifts
    afterwards — deriving at read time would let an unrelated account's
    creation repaint existing rows.
    """
    if explicit:
        return explicit
    return pick_next(await used_colors_for_bu(session, business_unit_id))


def effective(color: Optional[str], key: str) -> str:
    """The token the UI/Slack should actually paint: explicit choice or derived."""
    return color or derive(key)


def slack_hex(color: Optional[str], key: str) -> str:
    return COLOR_HEX[effective(color, key)]


def slack_emoji(color: Optional[str], key: str) -> str:
    return COLOR_EMOJI[effective(color, key)]
