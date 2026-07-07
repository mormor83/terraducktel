"""Cross-BU presence indicator for the top bar.

The UI polls POST /v1/presence every 30s with the BU slug it currently
has selected (or omits it for the "all" view). GET /v1/presence returns
the set of users seen in the last 60s — that's what powers the avatar
stack in the top bar so a user working in BU `home` can tell that someone
else is currently active in BU `partners` and avoid concurrent deploys.

Deliberately cross-BU even for non-superadmins: the whole point of the
feature is *cross*-BU awareness. We only leak identity (email +
display_name + the BU they're scoped to) — never page or run state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.db import get_db
from app.models.user import User
from app.models.user_presence import UserPresence

router = APIRouter(prefix="/api/v1/presence", tags=["presence"])


# Users seen in the last PRESENCE_WINDOW_SECONDS are considered online.
# 60s gives a 2x slack over the UI's 30s heartbeat cadence so a single
# missed ping doesn't make anyone vanish from the stack.
PRESENCE_WINDOW_SECONDS = 60


class PresencePing(BaseModel):
    # The BU slug currently selected in the UI. Optional: superadmins viewing
    # "all" send null. We don't validate it against business_units — a stale
    # slug just shows up unmodified in the UI for the 60s window.
    bu_slug: Optional[str] = None


class PresenceUser(BaseModel):
    user_id: str
    email: str
    display_name: Optional[str] = None
    bu_slug: Optional[str] = None
    last_seen_at: datetime


class PresenceListResponse(BaseModel):
    users: list[PresenceUser]


@router.post("", status_code=204)
async def ping(
    body: PresencePing,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
) -> None:
    """Upsert this user's presence row. Called every 30s by the UI."""
    row = await db.get(UserPresence, current.id)
    now = datetime.now(timezone.utc)
    if row is None:
        row = UserPresence(user_id=current.id, bu_slug=body.bu_slug, last_seen_at=now)
        db.add(row)
    else:
        row.bu_slug = body.bu_slug
        row.last_seen_at = now
    await db.commit()


@router.get("", response_model=PresenceListResponse)
async def list_active(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
) -> PresenceListResponse:
    """All users active in the last PRESENCE_WINDOW_SECONDS, across all BUs."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=PRESENCE_WINDOW_SECONDS)
    # Lazy GC: drop rows that haven't been refreshed in ~10x the window.
    # Keeps the table from growing to one row per user-who-ever-logged-in.
    gc_cutoff = datetime.now(timezone.utc) - timedelta(seconds=PRESENCE_WINDOW_SECONDS * 10)
    await db.execute(delete(UserPresence).where(UserPresence.last_seen_at < gc_cutoff))

    rows = (
        await db.execute(
            select(UserPresence, User)
            .join(User, User.id == UserPresence.user_id)
            .where(UserPresence.last_seen_at >= cutoff)
            .order_by(UserPresence.last_seen_at.desc())
        )
    ).all()
    return PresenceListResponse(
        users=[
            PresenceUser(
                user_id=p.user_id,
                email=u.email,
                display_name=u.display_name,
                bu_slug=p.bu_slug,
                last_seen_at=p.last_seen_at,
            )
            for (p, u) in rows
        ]
    )
