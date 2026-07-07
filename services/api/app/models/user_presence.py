"""Lightweight per-user presence row used by the top-bar avatar stack.

Each row tracks one user's most recent ping plus the BU slug they had
selected at the time. Rows are upserted by `POST /v1/presence` every 30s
from the UI and aged out client-side by filtering to "seen in the last
60s" — we don't keep history, just the latest sample per user.

Stored in its own table (not on `users`) so writes don't churn the
authoritative user row and so the presence sweep can hit a small,
hot table.
"""
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserPresence(Base):
    __tablename__ = "user_presence"

    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # BU slug the user was scoped to at last heartbeat. NULL when scoped to
    # "all" (superadmin view) so the UI can render an "All BUs" badge.
    bu_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
